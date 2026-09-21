from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.adapters.memory.agent_memory import InMemoryAgentMemoryStore
from app.adapters.memory.session_deletion import InMemorySessionDeletionJobStore
from app.domain.memory import (
    AGENT_MEMORY_TYPES,
    AgentMemoryAccessDenied,
    AgentMemoryRecord,
    AgentMemoryScope,
)
from app.runtime.session_deletion_worker import SessionDeletionWorker


NOW = datetime(2026, 9, 19, tzinfo=timezone.utc)


class MutableClock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now


class SessionStore:
    def __init__(self, *session_ids: str) -> None:
        self._session_ids = set(session_ids)

    def delete_session(self, session_id: str) -> int:
        if session_id not in self._session_ids:
            return 0
        self._session_ids.remove(session_id)
        return 1


def _scope(*, session_id: str, memory_type: str) -> AgentMemoryScope:
    return AgentMemoryScope(
        deployment_id="deployment-a",
        principal_id="principal-a",
        session_id=session_id,
        agent_id=f"{memory_type}-agent",
        memory_type=memory_type,
    )


def _record(memory_id: str, *, expires_at: datetime) -> AgentMemoryRecord:
    return AgentMemoryRecord(
        memory_id=memory_id,
        summary=f"bounded summary for {memory_id}",
        created_at=NOW,
        expires_at=expires_at,
    )


def test_write_recall_isolation_and_expiry_across_all_namespaces() -> None:
    clock = MutableClock()
    store = InMemoryAgentMemoryStore(clock=clock)

    for memory_type in AGENT_MEMORY_TYPES:
        scope = _scope(session_id="session-a", memory_type=memory_type)
        memory = _record(
            f"{memory_type}-memory",
            expires_at=NOW + timedelta(minutes=1),
        )
        port = store.bind(scope)

        assert port.remember(scope=scope, memory=memory) == memory
        assert port.recall(scope=scope) == (memory,)

        foreign_scope = scope.model_copy(update={"session_id": "session-b"})
        with pytest.raises(AgentMemoryAccessDenied):
            port.recall(scope=foreign_scope)

    clock.now = NOW + timedelta(minutes=1)

    for memory_type in AGENT_MEMORY_TYPES:
        scope = _scope(session_id="session-a", memory_type=memory_type)
        assert store.bind(scope).recall(scope=scope) == ()


def test_session_deletion_worker_purges_all_namespaces_and_tombstones_session() -> None:
    target_session = "session-delete"
    retained_session = "session-retain"
    store = InMemoryAgentMemoryStore(clock=lambda: NOW)

    for memory_type in AGENT_MEMORY_TYPES:
        for session_id in (target_session, retained_session):
            scope = _scope(session_id=session_id, memory_type=memory_type)
            store.bind(scope).remember(
                scope=scope,
                memory=_record(
                    f"{session_id}-{memory_type}",
                    expires_at=NOW + timedelta(hours=1),
                ),
            )

    jobs = InMemorySessionDeletionJobStore(
        job_id_factory=lambda: "delete-agent-memory-lifecycle"
    )
    jobs.request(target_session)
    completed = SessionDeletionWorker(
        job_store=jobs,
        session_store=SessionStore(target_session, retained_session),
        agent_memory_store=store,
    ).run_once()

    assert completed.status == "completed"
    assert completed.safe_counts["agent_private_memory_rows"] == len(
        AGENT_MEMORY_TYPES
    )

    for memory_type in AGENT_MEMORY_TYPES:
        deleted_scope = _scope(
            session_id=target_session,
            memory_type=memory_type,
        )
        retained_scope = _scope(
            session_id=retained_session,
            memory_type=memory_type,
        )
        assert store.bind(deleted_scope).recall(scope=deleted_scope) == ()
        assert len(store.bind(retained_scope).recall(scope=retained_scope)) == 1
        with pytest.raises(RuntimeError, match="cannot be recreated"):
            store.bind(deleted_scope).remember(
                scope=deleted_scope,
                memory=_record(
                    f"late-{memory_type}",
                    expires_at=NOW + timedelta(hours=1),
                ),
            )
