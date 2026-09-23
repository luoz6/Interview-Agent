from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.a2a.idempotency import build_agent_idempotency_key
from app.adapters.memory.execution_artifacts import InMemoryExecutionArtifactStore
from app.adapters.memory.agent_invocation_ledger import InMemoryAgentInvocationLedger
from app.adapters.memory.execution_path_binding import InMemoryExecutionPathBindingStore
from app.adapters.memory.scheduler_execution import InMemorySchedulerExecutionRepository
from app.adapters.memory.session_store import InterviewSessionStore
from app.application.interview.orchestration_cutover import ExecutionPathRouter
from app.application.interview.scheduler_production_entry import (
    MA9_ORCHESTRATION_VERSION,
    OrchestrationVersionMismatch,
    SchedulerProductionEntry,
    build_interview_execution,
)
from app.domain.interview.prep import InterviewPlan, InterviewQuestion
from app.domain.interview.scheduling import (
    ExecutionPlan,
    ExecutionState,
    ExecutionTaskDefinition,
    TaskRuntimeState,
    UserCommand,
)
from app.ports.execution_artifacts import ArtifactPayloadConflict
from tests.contracts.test_scheduler_wait_user_integration_contract import _scheduler
from tests.contracts.test_scheduler_invocation_crash_recovery_contract import (
    CrashAfter,
    _with_ledger,
)


class SimulatedCrash(BaseException):
    pass


class _DurableCommands:
    def __init__(self) -> None:
        self.records: dict[str, dict] = {}

    def enqueue(self, **kwargs) -> None:
        candidate = {
            "command_id": kwargs["command_id"],
            "command_type": kwargs["command_type"],
            "expected_version": kwargs["expected_version"],
            "answer_text": kwargs["payload"].get("answer_text"),
        }
        existing = self.records.get(kwargs["command_id"])
        if existing is not None and existing != candidate:
            raise ArtifactPayloadConflict(kwargs["command_id"])
        self.records[kwargs["command_id"]] = candidate

    def get(self, *, execution_id, command_id):
        del execution_id
        return self.records.get(command_id)


class _CrashFirstArtifactWrite:
    def __init__(self, delegate: InMemoryExecutionArtifactStore) -> None:
        self.delegate = delegate
        self.crashed = False

    def put_if_absent(self, artifact_ref, artifact):
        if not self.crashed:
            self.crashed = True
            raise SimulatedCrash("artifact write")
        return self.delegate.put_if_absent(artifact_ref, artifact)

    def get_required(self, artifact_ref):
        return self.delegate.get_required(artifact_ref)

    def exists(self, artifact_ref):
        return self.delegate.exists(artifact_ref)


class _CrashFirstStateSave:
    def __init__(self, delegate) -> None:
        self.delegate = delegate
        self.crashed = False

    def load(self, execution_id):
        return self.delegate.load(execution_id)

    def save(self, state):
        if not self.crashed:
            self.crashed = True
            raise SimulatedCrash("state save")
        return self.delegate.save(state)


def _plan() -> InterviewPlan:
    return InterviewPlan(
        title="T09",
        questions=[
            InterviewQuestion(
                id="q1",
                kind="technical",
                prompt="Explain backpressure.",
                focus="backpressure",
            )
        ],
    )


def _entry(repository: InMemorySchedulerExecutionRepository) -> SchedulerProductionEntry:
    return SchedulerProductionEntry(
        session_store=InterviewSessionStore(),
        execution_repository=repository,
        execution_path_router=ExecutionPathRouter(
            InMemoryExecutionPathBindingStore()
        ),
        scheduler_composer=lambda **_kwargs: SimpleNamespace(),
        id_generator=lambda: "ma9-t09",
    )


def _followup_pair() -> tuple[ExecutionTaskDefinition, ExecutionTaskDefinition]:
    followup = ExecutionTaskDefinition(
        task_id="followup:q1:1",
        capability="interview-examiner.generate-followup",
        agent_id="interview-examiner",
        skill="generate-followup",
        parameters={"question_id": "q1"},
    )
    evaluation = ExecutionTaskDefinition(
        task_id="evaluate-followup:q1:1",
        capability="interview-reviewer.evaluate-answer",
        agent_id="interview-reviewer",
        skill="evaluate-answer",
        parameters={"question_id": "q1"},
    )
    return followup, evaluation


def _waiting_answer_runtime():
    scheduler, state_store = _scheduler()
    scheduler.step("exec-wait")
    wait = scheduler.step("exec-wait").state.current_wait_handle
    artifacts = InMemoryExecutionArtifactStore()
    commands = _DurableCommands()
    scheduler.artifact_store = artifacts
    scheduler.durable_command_port = commands
    command = UserCommand(
        command_id="answer-crash-window",
        execution_id="exec-wait",
        wait_id=wait.wait_id,
        task_id=wait.task_id,
        question_id=wait.question_id,
        expected_revision=wait.issued_revision,
        payload={"answer_text": "Use bounded queues and backpressure."},
    )
    return scheduler, state_store, artifacts, commands, command


def test_new_execution_binds_ma9_version_in_immutable_plan() -> None:
    plan, _state = build_interview_execution(session_id="ma9", plan=_plan())

    assert plan.orchestration_version == MA9_ORCHESTRATION_VERSION
    with pytest.raises(ValidationError):
        plan.orchestration_version = "scheduler-other"


def test_pre_ma9_new_execution_routes_to_compatibility_runtime() -> None:
    repository = InMemorySchedulerExecutionRepository()
    legacy_plan = ExecutionPlan(
        execution_id="pre-ma9-new",
        interview_plan_ref="plan:legacy",
    )
    repository.create(
        legacy_plan,
        ExecutionState(execution_id=legacy_plan.execution_id),
    )
    entry = _entry(repository)
    entry.execution_path_router.claim_execution(legacy_plan.execution_id, "NEW")

    assert entry._load_ma9_plan(legacy_plan.execution_id) == legacy_plan


@pytest.mark.parametrize(
    "changes",
    (
        {
            "dynamic_task_definitions": _followup_pair(),
            "task_states": tuple(TaskRuntimeState(task_id=task.task_id) for task in _followup_pair()),
            "followups_total_used": 0,
            "followups_by_question": {},
            "replans_used": 0,
        },
        {
            "dynamic_task_definitions": (),
            "task_states": (),
            "followups_total_used": 1,
            "followups_by_question": {"q1": 1},
            "replans_used": 1,
        },
    ),
    ids=("pair_without_budget", "budget_without_pair"),
)
def test_partial_dynamic_pair_budget_state_is_impossible(changes: dict) -> None:
    with pytest.raises(ValidationError, match="follow-up pair budget invariant"):
        ExecutionState(execution_id="corrupt", **changes)


def test_complete_dynamic_pair_and_budget_round_trip() -> None:
    followup, evaluation = _followup_pair()
    state = ExecutionState(execution_id="valid").register_followup_pair(
        expected_revision=0,
        question_id="q1",
        followup=followup,
        evaluation=evaluation,
    )

    assert ExecutionState.model_validate(state.model_dump(mode="json")) == state


def test_execution_artifact_reference_with_missing_payload_fails_closed() -> None:
    store = InMemoryExecutionArtifactStore()

    with pytest.raises(Exception) as missing:
        store.get_required("missing/artifact")

    assert missing.value.__class__.__name__ == "ArtifactMissing"


def test_committed_ledger_with_missing_artifact_payload_fails_before_state_ref() -> None:
    ledger = InMemoryAgentInvocationLedger()
    crashed, _invoker, state_store = _with_ledger(
        CrashAfter(ledger, "commit"),
        worker_id="worker-crashed",
    )
    crashed.artifact_store = InMemoryExecutionArtifactStore()
    with pytest.raises(BaseException):
        crashed.step("exec-1")
    before = state_store.load("exec-1")

    recovered, _recovered_invoker, _ = _with_ledger(
        ledger,
        worker_id="worker-recovered",
    )
    recovered.state_store = state_store
    recovered.artifact_store = InMemoryExecutionArtifactStore()

    with pytest.raises(Exception) as missing:
        recovered.step("exec-1")

    assert missing.value.__class__.__name__ == "ArtifactMissing"
    assert state_store.load("exec-1") == before
    assert state_store.load("exec-1").artifact_refs == ()


def test_answer_crash_a_command_persisted_artifact_missing_replays_to_commit() -> None:
    scheduler, state_store, artifacts, commands, command = _waiting_answer_runtime()
    durable_artifacts = artifacts
    scheduler.artifact_store = _CrashFirstArtifactWrite(durable_artifacts)
    before = state_store.load(command.execution_id)

    with pytest.raises(SimulatedCrash):
        scheduler.accept_user_command(command.execution_id, command)

    assert commands.get(
        execution_id=command.execution_id,
        command_id=command.command_id,
    ) is not None
    assert scheduler.answer_artifact_for_command(
        command.execution_id, command.command_id
    ) is None
    assert state_store.load(command.execution_id) == before

    recovered = scheduler.accept_user_command(command.execution_id, command)

    assert recovered.accepted
    assert recovered.state.current_wait_handle is None
    assert scheduler.answer_artifact_for_command(
        command.execution_id, command.command_id
    ) is not None


def test_answer_crash_b_artifact_persisted_state_missing_repairs_state() -> None:
    scheduler, state_store, _artifacts, _commands, command = _waiting_answer_runtime()
    before = state_store.load(command.execution_id)
    scheduler.state_store = _CrashFirstStateSave(state_store)

    with pytest.raises(SimulatedCrash):
        scheduler.accept_user_command(command.execution_id, command)

    assert scheduler.answer_artifact_for_command(
        command.execution_id, command.command_id
    ) is not None
    assert state_store.load(command.execution_id) == before

    scheduler.state_store = state_store
    recovered = scheduler.accept_user_command(command.execution_id, command)

    assert recovered.outcome.disposition == "REPLAY"
    assert recovered.state.current_wait_handle is None
    assert recovered.state.latest_observation["answer_artifact_ref"] == (
        scheduler.answer_artifact_for_command(
            command.execution_id, command.command_id
        ).artifact_ref
    )
    assert state_store.load(command.execution_id) == recovered.state


def test_answer_crash_c_d_redelivery_is_idempotent_after_state_commit() -> None:
    scheduler, state_store, artifacts, _commands, command = _waiting_answer_runtime()
    accepted = scheduler.accept_user_command(command.execution_id, command)
    committed = state_store.load(command.execution_id)

    first_redelivery = scheduler.accept_user_command(command.execution_id, command)
    second_redelivery = scheduler.accept_user_command(command.execution_id, command)

    assert accepted.outcome.disposition == "ACCEPT"
    assert first_redelivery.outcome.disposition == "REPLAY"
    assert second_redelivery.outcome.disposition == "REPLAY"
    assert first_redelivery.state == second_redelivery.state == committed
    answer_refs = [
        ref.artifact_ref
        for ref in committed.artifact_refs
        if ref.artifact_type == "answer-artifact"
    ]
    assert len(answer_refs) == 1
    assert artifacts.exists(answer_refs[0])


def test_a2a_main_question_idempotency_is_scoped_to_frozen_intent() -> None:
    def key(question_id: str) -> str:
        return build_agent_idempotency_key(
            agent_id="interview-examiner",
            skill="generate-main-question",
            request={
                "intent": {
                    "question_id": question_id,
                    "fixed_question_text": f"Question {question_id}",
                }
            },
        )

    assert key("q1") == key("q1")
    assert key("q1") != key("q2")


def test_a2a_answer_review_idempotency_is_scoped_to_answer_artifact() -> None:
    def key(answer_ref: str) -> str:
        return build_agent_idempotency_key(
            agent_id="interview-reviewer",
            skill="evaluate-answer",
            request={
                "question_id": "q1",
                "answer_artifact_ref": answer_ref,
                "question_artifact_ref": "question:q1",
            },
        )

    assert key("answer:1") == key("answer:1")
    assert key("answer:1") != key("answer:2")
