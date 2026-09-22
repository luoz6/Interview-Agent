from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.a2a.contracts.evaluation import (
    EvaluationArtifactV2,
    EvaluationGapPayload,
)
from app.a2a.contracts.followup import FollowupArtifactPayload
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
from app.application.interview.scheduler_production_entry import (
    SchedulerProductionEntry,
)
from app.application.interview.session_commands import (
    DurableSessionStream,
    InterviewApplicationService,
    StreamingTurnService,
)
from app.application.scheduling import SchedulerApplicationCapability
from app.domain.interview.commands import SessionCommand
from app.domain.interview.prep import InterviewPlan, InterviewQuestion
from app.domain.interview.models import InterviewTurn
from app.domain.interview.scheduling import CapabilityDescriptor
from app.runtime.scheduler_streaming import (
    open_scheduler_answer_stream,
    open_scheduler_bootstrap_stream,
)


class _Catalog:
    def __init__(self) -> None:
        self.capabilities = {
            ("interview-examiner", "generate-main-question"): CapabilityDescriptor(
                agent_id="interview-examiner",
                skill="generate-main-question",
                description="main question",
                request_contract_id="generate-main-question-request",
                request_contract_version="v1",
                output_artifact_type="main-question-artifact",
                output_artifact_version="1.0",
                capability_version="v1",
            ),
            ("interview-reviewer", "evaluate-answer"): CapabilityDescriptor(
                agent_id="interview-reviewer",
                skill="evaluate-answer",
                description="review answer",
                request_contract_id="evaluate-answer-request",
                request_contract_version="v1",
                output_artifact_type="evaluation-artifact",
                output_artifact_version="evaluation-artifact-v2",
                capability_version="v1",
            ),
            ("interview-examiner", "generate-followup"): CapabilityDescriptor(
                agent_id="interview-examiner",
                skill="generate-followup",
                description="follow-up",
                request_contract_id="generate-followup-request",
                request_contract_version="v1",
                output_artifact_type="followup-artifact",
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
    def __init__(self, evidence_status: str) -> None:
        self.evidence_status = evidence_status
        self.calls: list[str] = []

    def invoke(self, *, agent_id, skill, request, execution_context=None):
        self.calls.append(skill)
        if skill == "generate-main-question":
            return MainQuestionArtifactPayload(
                question_id=request.intent["question_id"],
                question_text=request.intent["fixed_question_text"],
                render_mode="fixed",
                reason_code="fixed_plan_question",
            )
        if skill == "generate-followup":
            return FollowupArtifactPayload(
                question_id=request.question_id,
                gap_id=request.gap_id,
                followup_text="How do you handle overload?",
                reason_code=request.reason_code,
                focus=request.focus,
                policy_version=request.policy_version,
                evidence_ids=list(request.evidence_ids),
            )
        if self.evidence_status == "SUFFICIENT":
            return EvaluationArtifactV2(
                question_id=request.question_id,
                answer_artifact_ref=request.answer_artifact_ref,
                question_artifact_ref=request.question_artifact_ref,
                evaluation_status="EVALUATED",
                evidence_status="SUFFICIENT",
                score=85,
                summary="Enough evidence.",
                evaluation_policy_version="review-policy-v2",
            )
        return EvaluationArtifactV2(
            question_id=request.question_id,
            answer_artifact_ref=request.answer_artifact_ref,
            question_artifact_ref=request.question_artifact_ref,
            evaluation_status="EVALUATED",
            evidence_status="INSUFFICIENT",
            gap=EvaluationGapPayload(
                gap_id="gap:q1:overload",
                type="depth",
                focus="overload handling",
                reason="No overload evidence.",
            ),
            summary="More evidence required.",
            evaluation_policy_version="review-policy-v2",
        )


def _runtime(evidence_status: str):
    sessions = InterviewSessionStore()
    repository = InMemorySchedulerExecutionRepository()
    router = ExecutionPathRouter(InMemoryExecutionPathBindingStore())
    artifacts = InMemoryExecutionArtifactStore()
    invoker = _Invoker(evidence_status)

    def compose(*, plan, initial_state, execution_state_store, **_kwargs):
        return SimpleNamespace(
            scheduler=SchedulerApplicationCapability(
                state_store=execution_state_store,
                plan=plan,
                capability_port=_Catalog(),
                invocation_port=invoker,
                artifact_store=artifacts,
                evaluation_state_provider=sessions.get,
            )
        )

    entry = SchedulerProductionEntry(
        session_store=sessions,
        execution_repository=repository,
        execution_path_router=router,
        scheduler_composer=compose,
        id_generator=lambda: f"stream-{evidence_status.lower()}",
    )
    application = InterviewApplicationService(
        store=sessions,
        workflow_service_factory=lambda: SimpleNamespace(),
        publisher=SimpleNamespace(publish=lambda _event: None),
        report_job_store_factory=lambda: SimpleNamespace(),
        execution_path_router=router,
        scheduler_entry_factory=lambda: entry,
    )
    streaming = StreamingTurnService(
        application,
        scheduler_stream_factory=open_scheduler_answer_stream,
    )
    return entry, repository, artifacts, invoker, streaming


def _plan():
    return InterviewPlan(
        title="T08",
        questions=[
            InterviewQuestion(
                id=f"q{position}",
                kind="technical",
                prompt=f"Question {position}?",
                focus="systems",
            )
            for position in (1, 2)
        ],
    )


def _events(payloads: list[str]) -> list[dict]:
    return [
        json.loads(line.removeprefix("data: "))
        for payload in payloads
        for line in payload.splitlines()
        if line.startswith("data: ")
    ]


@pytest.mark.parametrize(
    ("evidence_status", "expected_skill", "expected_text"),
    (
        ("INSUFFICIENT", "generate-followup", "How do you handle overload?"),
        ("SUFFICIENT", "generate-main-question", "Question 2?"),
    ),
)
def test_new_answer_stream_cuts_over_examiner_question_lifecycle(
    evidence_status,
    expected_skill,
    expected_text,
):
    entry, repository, artifacts, invoker, streaming = _runtime(evidence_status)
    turn = entry.start(
        _plan(),
        job_description="backend",
        resume_text="systems",
        job_tags=[],
    )

    stream = streaming.prepare(
        SessionCommand.answer(
            turn.session_id,
            "I use explicit bounds.",
            command_id=f"answer-{evidence_status.lower()}",
        )
    )

    assert isinstance(stream, DurableSessionStream)
    payloads = list(stream.events)
    events = _events(payloads)
    assert [event["event_type"] for event in events] == [
        "STARTED",
        "DELTA",
        "COMPLETED",
    ]
    assert events[0]["skill"] == expected_skill
    assert events[1]["delta"] == expected_text
    assert events[-1]["final_text"] == expected_text
    assert artifacts.exists(events[-1]["artifact_ref"])
    state = repository.load(turn.session_id)
    assert state.task_state(events[-1]["task_id"]).status == "COMPLETED"
    assert state.current_wait_handle is not None
    assert state.current_wait_handle.task_id == events[-1]["task_id"]
    assert state.execution_status == "WAITING"
    assert invoker.calls[-1] == expected_skill


def test_completed_is_delivered_only_after_wait_user_is_durable():
    entry, repository, _artifacts, _invoker, streaming = _runtime("SUFFICIENT")
    turn = entry.start(
        _plan(),
        job_description="backend",
        resume_text="systems",
        job_tags=[],
    )
    stream = streaming.prepare(
        SessionCommand.answer(
            turn.session_id,
            "I use explicit bounds.",
            command_id="answer-completed-boundary",
        )
    )

    for payload in stream.events:
        event = _events([payload])[0]
        if event["event_type"] == "COMPLETED":
            state = repository.load(turn.session_id)
            assert state.execution_status == "WAITING"
            assert state.current_wait_handle is not None
            assert state.current_wait_handle.task_id == event["task_id"]


def test_disconnect_unsubscribes_but_question_commits_and_snapshot_recovers():
    entry, repository, artifacts, _invoker, streaming = _runtime("SUFFICIENT")
    turn = entry.start(
        _plan(),
        job_description="backend",
        resume_text="systems",
        job_tags=[],
    )
    stream = streaming.prepare(
        SessionCommand.answer(
            turn.session_id,
            "I use explicit bounds.",
            command_id="answer-disconnect",
        )
    )
    iterator = iter(stream.events)
    first = next(iterator)
    assert "\"event_type\": \"STARTED\"" in first
    iterator.close()

    worker = stream.owner
    assert worker.join(timeout=2)
    state = repository.load(turn.session_id)
    assert state.current_wait_handle is not None
    question_ref = next(
        ref
        for ref in state.artifact_refs
        if ref.task_id == state.current_wait_handle.task_id
    )
    assert artifacts.exists(question_ref.artifact_ref)
    snapshot = entry.snapshot(turn.session_id)
    assert snapshot["current_question"]["id"] == "q2"
    assert snapshot["current_question"]["prompt"] == "Question 2?"


def test_scheduler_bootstrap_stream_uses_same_main_question_lifecycle():
    entry, repository, artifacts, invoker, _streaming = _runtime("SUFFICIENT")
    turn = entry.start(
        _plan(),
        job_description="backend",
        resume_text="systems",
        job_tags=[],
        bootstrap=False,
    )
    before = entry.snapshot(turn.session_id)
    assert before["status"] == "preparing_first_question"
    assert before["current_question"] is None

    stream = open_scheduler_bootstrap_stream(entry, turn.session_id)
    events = _events(list(stream.events))

    assert [event["event_type"] for event in events] == [
        "STARTED",
        "DELTA",
        "COMPLETED",
    ]
    assert events[-1]["skill"] == "generate-main-question"
    assert events[-1]["final_text"] == "Question 1?"
    assert artifacts.exists(events[-1]["artifact_ref"])
    state = repository.load(turn.session_id)
    assert state.current_wait_handle is not None
    assert state.execution_status == "WAITING"
    assert invoker.calls == ["generate-main-question"]
    snapshot = entry.snapshot(turn.session_id)
    assert snapshot["current_question"]["prompt"] == "Question 1?"


def test_final_answer_stream_keeps_done_boundary_without_examiner_event():
    entry = SimpleNamespace(
        prepare_streaming_answer=lambda _command: None,
        project_turn=lambda session_id: InterviewTurn(
            session_id=session_id,
            current_question=None,
            follow_up=None,
            status="finished",
        ),
    )

    stream = open_scheduler_answer_stream(
        entry,
        SessionCommand.answer(
            "final-session",
            "final answer",
            command_id="final-command",
        ),
    )

    body = "".join(stream.events)
    assert body.startswith("event: done\n")
    assert '"status": "finished"' in body
