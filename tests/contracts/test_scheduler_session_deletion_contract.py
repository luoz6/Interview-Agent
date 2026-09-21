from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.a2a.protocol import A2ATask
from app.a2a.server import LocalA2AServer
from app.adapters.memory.agent_invocation_ledger import InMemoryAgentInvocationLedger
from app.adapters.memory.agent_memory import InMemoryAgentMemoryStore
from app.adapters.memory.session_deletion import InMemorySessionDeletionJobStore
from app.application.interview.session_deletion import SessionDeletionService
from app.application.scheduling import InMemoryExecutionStateStore
from app.domain.agents.artifacts import DomainArtifact
from app.domain.memory import AgentMemoryRecord, AgentMemoryScope
from app.domain.interview.scheduling import (
    ExecutionState,
    InvocationIdentity,
    InvocationLedgerEntry,
    TaskRuntimeState,
    WaitHandle,
)
from app.runtime.session_deletion_worker import SessionDeletionWorker
from tests.session_fixtures import make_deletion_session_store


class RecordingContextArtifacts:
    def __init__(self) -> None:
        self.calls = []

    def delete_owner_refs(self, *, owner_type, owner_key):
        self.calls.append((owner_type, owner_key))
        return int(owner_type == "interview_session")


class RecordingReportArtifacts:
    def __init__(self) -> None:
        self.deleted = []

    def list_jobs(self, session_id):
        return ()

    def delete_session_history(self, session_id):
        self.deleted.append(session_id)
        return 1


def _entry(execution_id: str, task_id: str) -> InvocationLedgerEntry:
    return InvocationLedgerEntry(
        identity=InvocationIdentity(
            execution_id=execution_id,
            task_id=task_id,
            logical_attempt=1,
        ),
        agent_id="interview-examiner",
        skill="generate-followup",
        request_digest=f"sha256:{task_id}",
    )


def test_session_deletion_purges_scheduler_agent_and_artifact_owners():
    session_id = "scheduler-session-delete"
    business_sessions, _ = make_deletion_session_store(session_id=session_id)
    deletion_jobs = InMemorySessionDeletionJobStore(
        job_id_factory=lambda: "delete-scheduler-session"
    )
    SessionDeletionService(
        session_store=business_sessions,
        job_store=deletion_jobs,
    ).request(session_id)

    waiting_state = ExecutionState(
        execution_id=session_id,
        execution_status="WAITING",
        task_states=(
            TaskRuntimeState(task_id="followup-1", status="WAITING", attempt=1),
        ),
        current_wait_handle=WaitHandle(
            wait_id="wait-1",
            execution_id=session_id,
            task_id="followup-1",
            question_id="question-1",
            issued_revision=3,
        ),
    )
    other_state = ExecutionState(execution_id="other-session")
    execution_states = InMemoryExecutionStateStore(waiting_state, other_state)

    ledger = InMemoryAgentInvocationLedger()
    pending = _entry(session_id, "followup-1")
    running = _entry(session_id, "evaluation-1")
    other_invocation = _entry("other-session", "followup-1")
    ledger.prepare(pending)
    ledger.prepare(running)
    ledger.acquire(
        running.identity,
        owner_id="worker-old",
        lease_seconds=60,
    )
    ledger.prepare(other_invocation)

    now = datetime(2026, 9, 19, tzinfo=timezone.utc)
    agent_memory_store = InMemoryAgentMemoryStore(clock=lambda: now)

    def memory_scope(session: str, agent_id: str, memory_type: str):
        return AgentMemoryScope(
            deployment_id="deployment-a",
            principal_id="principal-a",
            session_id=session,
            agent_id=agent_id,
            memory_type=memory_type,
        )

    def memory_record(memory_id: str):
        return AgentMemoryRecord(
            memory_id=memory_id,
            summary=f"summary for {memory_id}",
            created_at=now,
            expires_at=now + timedelta(hours=1),
        )

    deleted_examiner_scope = memory_scope(
        session_id,
        "interview-examiner",
        "examiner",
    )
    deleted_reviewer_scope = memory_scope(
        session_id,
        "interview-reviewer",
        "reviewer",
    )
    retained_scope = memory_scope(
        "other-session",
        "interview-reviewer",
        "reviewer",
    )
    for scope, record in (
        (deleted_examiner_scope, memory_record("examiner-memory")),
        (deleted_reviewer_scope, memory_record("reviewer-memory")),
        (retained_scope, memory_record("retained-memory")),
    ):
        agent_memory_store.bind(scope).remember(scope=scope, memory=record)

    agent_sessions = LocalA2AServer()
    calls = []

    def handler(request, execution_context):
        calls.append(request["value"])
        return DomainArtifact(artifact_type="followup-artifact")

    agent_sessions.register(
        agent_id="interview-examiner",
        skill="generate-followup",
        handler=handler,
    )
    for task_id, context_id, value in (
        ("task-delete-1", session_id, "delete-1"),
        ("task-delete-2", session_id, "delete-2"),
        ("task-keep", "other-session", "keep"),
    ):
        agent_sessions.submit(
            A2ATask(
                task_id=task_id,
                agent_id="interview-examiner",
                skill="generate-followup",
                context_id=context_id,
                input={"value": value},
            )
        )

    context_artifacts = RecordingContextArtifacts()
    report_artifacts = RecordingReportArtifacts()
    completed = SessionDeletionWorker(
        job_store=deletion_jobs,
        session_store=business_sessions,
        execution_state_store=execution_states,
        agent_session_store=agent_sessions,
        agent_invocation_ledger=ledger,
        agent_memory_store=agent_memory_store,
        context_artifact_store=context_artifacts,
        report_artifact_store=report_artifacts,
    ).run_once()

    assert completed.status == "completed"
    assert completed.safe_counts["scheduler_execution_states"] == 1
    assert completed.safe_counts["agent_session_rows"] == 2
    assert completed.safe_counts["agent_invocation_rows"] == 2
    assert completed.safe_counts["agent_private_memory_rows"] == 2
    assert completed.safe_counts["artifact_owner_refs"] == 1
    assert completed.safe_counts["report_history_rows"] == 1

    with pytest.raises(KeyError):
        execution_states.load(session_id)
    with pytest.raises(RuntimeError, match="cannot be recreated"):
        execution_states.save(waiting_state)
    assert execution_states.load("other-session") == other_state

    assert ledger.get(pending.identity) is None
    assert ledger.get(running.identity) is None
    assert ledger.get(other_invocation.identity) == other_invocation
    with pytest.raises(RuntimeError, match="cannot prepare"):
        ledger.prepare(pending)

    assert agent_memory_store.bind(deleted_examiner_scope).recall(
        scope=deleted_examiner_scope
    ) == ()
    assert agent_memory_store.bind(deleted_reviewer_scope).recall(
        scope=deleted_reviewer_scope
    ) == ()
    assert agent_memory_store.bind(retained_scope).recall(scope=retained_scope) == (
        memory_record("retained-memory"),
    )
    with pytest.raises(RuntimeError, match="cannot be recreated"):
        agent_memory_store.bind(deleted_examiner_scope).remember(
            scope=deleted_examiner_scope,
            memory=memory_record("late-memory"),
        )

    assert agent_sessions.delete_session_history(session_id) == 0
    assert agent_sessions.delete_session_history("other-session") == 1
    assert context_artifacts.calls == [("interview_session", session_id)]
    assert report_artifacts.deleted == [session_id]
