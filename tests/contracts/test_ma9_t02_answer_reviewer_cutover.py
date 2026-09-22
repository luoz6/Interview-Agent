from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.a2a.contracts.evaluation import EvaluationArtifactPayload
from app.a2a.contracts.main_question import MainQuestionArtifactPayload
from app.adapters.memory.execution_artifacts import InMemoryExecutionArtifactStore
from app.adapters.memory.execution_path_binding import (
    InMemoryExecutionPathBindingStore,
)
from app.adapters.memory.scheduler_execution import (
    InMemorySchedulerExecutionRepository,
)
from app.adapters.memory.session_store import InterviewSessionStore
from app.application.interview.orchestration_cutover import ExecutionPathRouter
from app.application.interview.scheduler_production_entry import SchedulerProductionEntry
from app.application.interview.session_commands import (
    InterviewApplicationService,
    LegacySessionStream,
    StreamingTurnService,
)
from app.application.scheduling import SchedulerApplicationCapability
from app.domain.interview.commands import SessionCommand
from app.domain.interview.prep import InterviewPlan, InterviewQuestion
from app.domain.interview.scheduling import (
    AnswerArtifact,
    CapabilityDescriptor,
    ExecutionPlan,
    ExecutionState,
    TaskRuntimeState,
    UserCommand,
    WaitHandle,
    answer_artifact_ref,
)
from app.ports.execution_artifacts import ArtifactPayloadConflict
from app.adapters.postgres.schema_contract import (
    LATEST_RUNTIME_MIGRATION,
    required_columns_for_relation,
)


class _Catalog:
    def __init__(self) -> None:
        self.capabilities = {
            ("interview-examiner", "generate-main-question"): CapabilityDescriptor(
                agent_id="interview-examiner",
                skill="generate-main-question",
                description="Generate a main question.",
                request_contract_id="generate-main-question-request",
                request_contract_version="v1",
                output_artifact_type="main-question-artifact",
                output_artifact_version="1.0",
                capability_version="v1",
            ),
            ("interview-reviewer", "evaluate-answer"): CapabilityDescriptor(
                agent_id="interview-reviewer",
                skill="evaluate-answer",
                description="Evaluate one answer.",
                request_contract_id="evaluate-answer-request",
                request_contract_version="v1",
                output_artifact_type="evaluation-artifact",
                output_artifact_version="1.0",
                capability_version="v1",
            ),
        }

    def resolve(self, *, agent_id, skill, capability_version=None):
        return self.capabilities.get((agent_id, skill))

    def validate_compatibility(self, **kwargs):
        return self.resolve(
            agent_id=kwargs["agent_id"],
            skill=kwargs["skill"],
        ) is not None


class _Invoker:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, object]] = []

    def invoke(self, *, agent_id, skill, request, execution_context=None):
        self.calls.append((agent_id, skill, request))
        if skill == "generate-main-question":
            return MainQuestionArtifactPayload(
                question_id=request.intent["question_id"],
                question_text=request.intent["fixed_question_text"],
                render_mode="fixed",
                reason_code="fixed_plan_question",
            )
        assert any(
            message.get("content") == "bounded by capacity"
            for message in request.state["messages"]
        )
        return EvaluationArtifactPayload(
            question_id=request.question_id,
            score=82,
            evaluation_policy_version="ma9-t02-test",
        )


def _plan() -> InterviewPlan:
    return InterviewPlan(
        title="MA9 T02",
        questions=[
            InterviewQuestion(
                id=f"q{position}",
                kind="technical",
                prompt=f"Question {position}?",
                focus=f"focus-{position}",
            )
            for position in range(1, 3)
        ],
    )


def _production_runtime():
    session_store = InterviewSessionStore()
    repository = InMemorySchedulerExecutionRepository()
    router = ExecutionPathRouter(InMemoryExecutionPathBindingStore())
    artifact_store = InMemoryExecutionArtifactStore()
    invoker = _Invoker()

    def compose(*, plan, initial_state, execution_state_store, **_kwargs):
        return SimpleNamespace(
            scheduler=SchedulerApplicationCapability(
                state_store=execution_state_store,
                plan=plan,
                capability_port=_Catalog(),
                invocation_port=invoker,
                artifact_store=artifact_store,
                evaluation_state_provider=session_store.get,
            )
        )

    entry = SchedulerProductionEntry(
        session_store=session_store,
        execution_repository=repository,
        execution_path_router=router,
        scheduler_composer=compose,
        id_generator=lambda: "ma9-t02-execution",
    )
    return entry, repository, artifact_store, invoker


def test_production_answer_persists_artifact_and_invokes_reviewer_once():
    entry, repository, artifact_store, invoker = _production_runtime()
    turn = entry.start(
        _plan(),
        job_description="backend",
        resume_text="built queues",
        job_tags=[],
    )
    command = SessionCommand.answer(
        turn.session_id,
        "bounded by capacity",
        command_id="answer-1",
    )

    entry.execute(command)

    ref = answer_artifact_ref(turn.session_id, "answer-1")
    artifact = artifact_store.get_required(ref)
    state = repository.load(turn.session_id)
    assert isinstance(artifact, AnswerArtifact)
    assert artifact.answer_text == "bounded by capacity"
    assert any(item.artifact_ref == ref for item in state.artifact_refs)
    assert "bounded by capacity" not in json.dumps(
        state.model_dump(mode="json"), ensure_ascii=False
    )
    assert state.task_state("evaluate:1:q1").status == "COMPLETED"
    reviewer_calls = [call for call in invoker.calls if call[1] == "evaluate-answer"]
    assert len(reviewer_calls) == 1

    before_replay = repository.load(turn.session_id)
    entry.execute(command)
    assert repository.load(turn.session_id) == before_replay
    reviewer_calls = [call for call in invoker.calls if call[1] == "evaluate-answer"]
    assert len(reviewer_calls) == 1


def test_streaming_answer_uses_same_reviewer_cutover_path():
    entry, repository, artifact_store, invoker = _production_runtime()
    turn = entry.start(
        _plan(),
        job_description="backend",
        resume_text="built queues",
        job_tags=[],
    )
    application = InterviewApplicationService(
        store=entry.session_store,
        workflow_service_factory=lambda: SimpleNamespace(),
        publisher=SimpleNamespace(publish=lambda _event: None),
        report_job_store_factory=lambda: SimpleNamespace(),
        execution_path_router=entry.execution_path_router,
        scheduler_entry_factory=lambda: entry,
    )

    stream = StreamingTurnService(application).prepare(
        SessionCommand.answer(
            turn.session_id,
            "bounded by capacity",
            command_id="stream-answer-1",
        )
    )

    assert isinstance(stream, LegacySessionStream)
    assert list(stream.events)[-1].event == "done"
    ref = answer_artifact_ref(turn.session_id, "stream-answer-1")
    assert artifact_store.get_required(ref).answer_text == "bounded by capacity"
    assert repository.load(turn.session_id).task_state(
        "evaluate:1:q1"
    ).status == "COMPLETED"
    assert len([call for call in invoker.calls if call[1] == "evaluate-answer"]) == 1

def test_answer_identity_is_deterministic_and_payload_conflicts_fail_closed():
    first_ref = answer_artifact_ref("execution-1", "command-1")
    assert first_ref == answer_artifact_ref("execution-1", "command-1")
    assert first_ref != answer_artifact_ref("execution-1", "command-2")
    store = InMemoryExecutionArtifactStore()
    base = AnswerArtifact(
        artifact_ref=first_ref,
        execution_id="execution-1",
        question_id="q1",
        source_task_id="main:1:q1",
        answer_kind="MAIN",
        answer_text="first",
        command_id="command-1",
        wait_id="wait-1",
        submitted_revision=1,
        submitted_at="2026-09-22T00:00:00Z",
    )
    store.put_if_absent(first_ref, base)

    with pytest.raises(ArtifactPayloadConflict):
        store.put_if_absent(
            first_ref,
            base.model_copy(update={"answer_text": "different"}),
        )


def test_execution_artifact_postgres_contract_is_v34():
    assert LATEST_RUNTIME_MIGRATION.migration_id == (
        "execution_artifact_store_v1_v34"
    )
    assert required_columns_for_relation("interview_execution_artifacts") == {
        "artifact_ref",
        "execution_id",
        "artifact_type",
        "schema_version",
        "payload_sha256",
        "payload_json",
        "created_at",
    }


def test_replay_repairs_state_after_artifact_commit_before_state_commit():
    execution_id = "repair-execution"
    command_id = "repair-command"
    ref = answer_artifact_ref(execution_id, command_id)
    wait = WaitHandle(
        wait_id="repair-wait",
        execution_id=execution_id,
        task_id="main:1:q1",
        question_id="q1",
        issued_revision=1,
    )
    state = ExecutionState(
        execution_id=execution_id,
        revision=1,
        task_states=(TaskRuntimeState(task_id="main:1:q1", status="COMPLETED"),),
        current_wait_handle=wait,
        execution_status="WAITING",
    )
    artifact_store = InMemoryExecutionArtifactStore()
    artifact_store.put_if_absent(
        ref,
        AnswerArtifact(
            artifact_ref=ref,
            execution_id=execution_id,
            question_id="q1",
            source_task_id="main:1:q1",
            answer_kind="MAIN",
            answer_text="recover me",
            command_id=command_id,
            wait_id=wait.wait_id,
            submitted_revision=wait.issued_revision,
            submitted_at="2026-09-22T00:00:00Z",
        ),
    )
    state_store = SimpleNamespace(
        load=lambda _execution_id: state,
        save=lambda saved: setattr(state_store, "saved", saved),
    )
    scheduler = SchedulerApplicationCapability(
        state_store=state_store,
        plan=ExecutionPlan(execution_id=execution_id, interview_plan_ref="plan-1"),
        capability_port=SimpleNamespace(),
        invocation_port=SimpleNamespace(),
        artifact_store=artifact_store,
    )

    result = scheduler.accept_user_command(
        execution_id,
        UserCommand(
            command_id=command_id,
            execution_id=execution_id,
            wait_id=wait.wait_id,
            task_id=wait.task_id,
            question_id=wait.question_id,
            expected_revision=wait.issued_revision,
            payload={"answer_text": "recover me"},
        ),
    )

    assert result.outcome.disposition == "REPLAY"
    assert result.state.current_wait_handle is None
    assert result.state.artifact_refs[-1].artifact_ref == ref
    assert state_store.saved == result.state
