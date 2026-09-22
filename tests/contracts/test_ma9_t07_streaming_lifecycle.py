from __future__ import annotations

from threading import Event
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.api.shared.errors import raise_value_error
from app.domain.interview.commands import SessionCommand
from app.application.scheduling import (
    InMemoryExecutionStateStore,
    SchedulerApplicationCapability,
)
from app.application.interview.scheduler_production_entry import (
    SchedulerProductionEntry,
)
from app.domain.agent_streaming import (
    AgentStreamEvent,
    AgentStreamIdentity,
    CommittedStreamArtifact,
)
from app.domain.agents.artifacts import DomainArtifact
from app.domain.interview.scheduling import (
    CapabilityDescriptor,
    ExecutionPlan,
    ExecutionState,
    ExecutionTaskDefinition,
    TaskRuntimeState,
    UserCommand,
)
from app.runtime.agent_streaming import AgentStreamInvocationWorker


def _identity(logical_attempt: int = 1) -> AgentStreamIdentity:
    return AgentStreamIdentity(
        execution_id="exec-stream",
        task_id="question:q1",
        logical_attempt=logical_attempt,
        agent_id="interview-examiner",
        skill="generate-main-question",
        question_id="q1",
    )


def test_agent_stream_event_freezes_envelope_and_event_identity():
    event = AgentStreamEvent(
        event_type="DELTA",
        execution_id="exec-stream",
        task_id="question:q1",
        logical_attempt=1,
        agent_id="interview-examiner",
        skill="generate-main-question",
        stream_id="stream-1",
        sequence=2,
        event_id="stream-1:2",
        question_id="q1",
        delta="hello",
        emitted_at="2026-09-22T00:00:00Z",
    )

    assert event.schema_version == "agent-stream-event-v1"
    assert set(AgentStreamEvent.model_fields) == {
        "schema_version",
        "event_type",
        "execution_id",
        "task_id",
        "logical_attempt",
        "agent_id",
        "skill",
        "stream_id",
        "sequence",
        "event_id",
        "question_id",
        "delta",
        "final_text",
        "artifact_ref",
        "error_code",
        "retryable",
        "emitted_at",
    }

    with pytest.raises(ValidationError):
        event.model_copy(update={"event_id": "wrong:2"}).model_validate(
            {**event.model_dump(), "event_id": "wrong:2"}
        )


def test_stream_identity_is_stable_per_attempt_and_changes_on_retry():
    first = _identity(1)
    replay = _identity(1)
    retry = _identity(2)

    assert first.stream_id == replay.stream_id
    assert first.stream_id != retry.stream_id


def test_runtime_worker_emits_monotonic_lifecycle_and_commits_canonical_text():
    committed: list[str] = []

    def commit(generated_text: str) -> CommittedStreamArtifact:
        committed.append(generated_text)
        return CommittedStreamArtifact(
            artifact_ref="question/exec-stream/q1",
            final_text=generated_text.strip(),
        )

    worker = AgentStreamInvocationWorker(
        identity=_identity(),
        invoke=lambda: iter(["  canonical", " question  "]),
        commit=commit,
    )
    observer = worker.subscribe(buffer_size=10)

    worker.start()
    assert worker.join(timeout=2)
    events = observer.drain()

    assert [event.event_type for event in events] == [
        "STARTED",
        "DELTA",
        "DELTA",
        "COMPLETED",
    ]
    assert [event.sequence for event in events] == [1, 2, 3, 4]
    assert [event.event_id for event in events] == [
        f"{worker.stream_id}:{sequence}" for sequence in range(1, 5)
    ]
    assert committed == ["  canonical question  "]
    assert events[-1].final_text == "canonical question"
    assert events[-1].artifact_ref == "question/exec-stream/q1"


def test_disconnect_only_unsubscribes_and_invocation_still_commits():
    release = Event()
    committed: list[str] = []

    def chunks():
        yield "first"
        assert release.wait(timeout=2)
        yield " second"

    worker = AgentStreamInvocationWorker(
        identity=_identity(),
        invoke=chunks,
        commit=lambda text: (
            committed.append(text)
            or CommittedStreamArtifact(
                artifact_ref="question/exec-stream/q1",
                final_text=text,
            )
        ),
    )
    observer = worker.subscribe(buffer_size=10)
    worker.start()
    assert observer.next_event(timeout=2).event_type == "STARTED"
    assert observer.next_event(timeout=2).event_type == "DELTA"

    observer.close()
    release.set()

    assert worker.join(timeout=2)
    assert committed == ["first second"]
    assert worker.succeeded


def test_observer_callback_failure_isolated_from_invocation_and_commit():
    committed: list[str] = []
    worker = AgentStreamInvocationWorker(
        identity=_identity(),
        invoke=lambda: iter(["one", "two"]),
        commit=lambda text: (
            committed.append(text)
            or CommittedStreamArtifact(
                artifact_ref="question/exec-stream/q1",
                final_text=text,
            )
        ),
    )
    worker.subscribe(
        buffer_size=2,
        callback=lambda _event: (_ for _ in ()).throw(
            RuntimeError("observer failed")
        ),
    )

    worker.start()

    assert worker.join(timeout=2)
    assert committed == ["onetwo"]
    assert worker.succeeded


def test_slow_observer_callback_does_not_block_invocation_or_commit():
    release_callback = Event()
    committed: list[str] = []
    worker = AgentStreamInvocationWorker(
        identity=_identity(),
        invoke=lambda: iter(["one", "two", "three"]),
        commit=lambda text: (
            committed.append(text)
            or CommittedStreamArtifact(
                artifact_ref="question/exec-stream/q1",
                final_text=text,
            )
        ),
    )
    observer = worker.subscribe(
        buffer_size=1,
        callback=lambda _event: release_callback.wait(timeout=2),
    )

    worker.start()

    assert worker.join(timeout=0.5)
    assert observer.disconnected
    assert committed == ["onetwothree"]
    assert worker.succeeded
    release_callback.set()


def test_full_bounded_observer_buffer_disconnects_only_observer():
    committed: list[str] = []
    worker = AgentStreamInvocationWorker(
        identity=_identity(),
        invoke=lambda: iter(["one", "two", "three"]),
        commit=lambda text: (
            committed.append(text)
            or CommittedStreamArtifact(
                artifact_ref="question/exec-stream/q1",
                final_text=text,
            )
        ),
    )
    slow_observer = worker.subscribe(buffer_size=1)

    worker.start()

    assert worker.join(timeout=2)
    assert slow_observer.disconnected
    assert committed == ["onetwothree"]
    assert worker.succeeded


def test_invocation_failure_emits_failed_without_committing():
    committed: list[str] = []

    def fail():
        raise RuntimeError("provider failed")
        yield "unreachable"

    worker = AgentStreamInvocationWorker(
        identity=_identity(),
        invoke=fail,
        commit=lambda text: (
            committed.append(text)
            or CommittedStreamArtifact(
                artifact_ref="question/exec-stream/q1",
                final_text=text,
            )
        ),
        retryable=True,
    )
    observer = worker.subscribe(buffer_size=10)

    worker.start()
    assert worker.join(timeout=2)
    events = observer.drain()

    assert [event.event_type for event in events] == ["STARTED", "FAILED"]
    assert events[-1].error_code == "RuntimeError"
    assert events[-1].retryable is True
    assert committed == []
    assert not worker.succeeded


def test_completed_hook_runs_after_completed_event_is_published():
    observed: list[str] = []
    worker = AgentStreamInvocationWorker(
        identity=_identity(),
        invoke=lambda: iter(["question"]),
        commit=lambda text: CommittedStreamArtifact(
            artifact_ref="question/exec-stream/q1",
            final_text=text,
        ),
        after_completed=lambda: observed.append("boundary"),
    )
    publish = worker._broker.publish

    def record_publish(event):
        observed.append(event.event_type)
        publish(event)

    worker._broker.publish = record_publish

    worker.start()
    assert worker.join(timeout=2)

    assert "COMPLETED" in observed
    assert observed.index("COMPLETED") < observed.index("boundary")


class _Catalog:
    def __init__(self, capability):
        self.capability = capability

    def resolve(self, *, agent_id, skill, capability_version=None):
        return self.capability

    def validate_compatibility(self, **_kwargs):
        return True


class _BlockingInvoker:
    def __init__(self):
        self.started = Event()
        self.release = Event()

    def invoke(self, **_kwargs):
        self.started.set()
        assert self.release.wait(timeout=2)
        return DomainArtifact(artifact_type="followup-artifact")


def test_answer_during_running_rejects_question_not_ready():
    capability = CapabilityDescriptor(
        agent_id="interview-examiner",
        skill="generate-followup",
        description="Generate the next follow-up question.",
        request_contract_id="generate-followup-request",
        request_contract_version="v1",
        output_artifact_type="followup-artifact",
        output_artifact_version="1.0",
        capability_version="v1",
    )
    task = ExecutionTaskDefinition(
        task_id="question:q1",
        capability="interview.followup",
        agent_id=capability.agent_id,
        skill=capability.skill,
        input_contract=capability.request_contract_id,
        output_contract=capability.output_artifact_type,
        parameters={"question_id": "q1", "focus": "queues"},
    )
    plan = ExecutionPlan(
        execution_id="exec-running",
        interview_plan_ref="plan-1",
        task_definitions=(task,),
    )
    store = InMemoryExecutionStateStore(
        ExecutionState(
            execution_id=plan.execution_id,
            task_states=(TaskRuntimeState(task_id=task.task_id),),
        )
    )
    invoker = _BlockingInvoker()
    scheduler = SchedulerApplicationCapability(
        state_store=store,
        plan=plan,
        capability_port=_Catalog(capability),
        invocation_port=invoker,
    )

    import threading

    scheduler_thread = threading.Thread(
        target=scheduler.step,
        args=(plan.execution_id,),
    )
    scheduler_thread.start()
    assert invoker.started.wait(timeout=2)
    running = store.load(plan.execution_id)
    assert running.task_state(task.task_id).status == "RUNNING"

    command = UserCommand(
        command_id="answer-too-soon",
        execution_id=plan.execution_id,
        wait_id="not-issued",
        task_id=task.task_id,
        question_id="q1",
        expected_revision=running.revision,
        payload={"answer_text": "too soon"},
    )
    rejected = scheduler.accept_user_command(plan.execution_id, command)

    invoker.release.set()
    scheduler_thread.join(timeout=2)
    assert rejected.outcome.disposition == "REJECT"
    assert rejected.outcome.reason_code == "QUESTION_NOT_READY"


def test_production_entry_rejects_answer_while_question_task_is_running():
    state = ExecutionState(
        execution_id="exec-running-entry",
        task_states=(
            TaskRuntimeState(task_id="question:q1", status="RUNNING", attempt=1),
        ),
    )
    scheduler = SimpleNamespace(
        answer_artifact_for_command=lambda *_args: None,
        command_store=SimpleNamespace(get=lambda _command_id: None),
    )
    entry = SchedulerProductionEntry(
        session_store=object(),
        execution_repository=SimpleNamespace(
            load_plan=lambda _execution_id: ExecutionPlan(
                execution_id="exec-running-entry",
                interview_plan_ref="plan:running",
                orchestration_version="scheduler-ma9-v1",
            ),
            load=lambda _execution_id: state,
        ),
        execution_path_router=SimpleNamespace(
            require_execution_path=lambda *_args: None,
        ),
        scheduler_composer=lambda **_kwargs: SimpleNamespace(
            scheduler=scheduler,
        ),
    )

    with pytest.raises(ValueError, match="^QUESTION_NOT_READY$"):
        entry.accept_answer(
            SessionCommand.answer(
                "exec-running-entry",
                "too soon",
                command_id="answer-too-soon",
            )
        )


def test_question_not_ready_maps_to_http_conflict():
    with pytest.raises(HTTPException) as raised:
        raise_value_error(ValueError("QUESTION_NOT_READY"))

    assert raised.value.status_code == 409
    assert raised.value.detail == {"code": "QUESTION_NOT_READY"}
