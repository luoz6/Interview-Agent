"""In-process adapter for logically isolated Agent-private memory."""

from __future__ import annotations

from datetime import datetime, timezone
from threading import RLock
from typing import Callable

from app.domain.memory.agent import (
    AgentMemoryAccessDenied,
    AgentMemoryRecord,
    AgentMemoryScope,
)
from app.domain.memory.agent_context import (
    DEFAULT_AGENT_MEMORY_CONTEXT_POLICY,
    AgentMemoryContextPolicy,
)


class InMemoryAgentMemoryStore:
    """One physical store serving all logical Agent memory namespaces."""

    def __init__(
        self,
        *,
        policy: AgentMemoryContextPolicy = DEFAULT_AGENT_MEMORY_CONTEXT_POLICY,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._entries: dict[AgentMemoryScope, list[AgentMemoryRecord]] = {}
        self._deleted_sessions: set[str] = set()
        self._policy = policy
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._lock = RLock()

    def bind(self, scope: AgentMemoryScope) -> ScopedAgentMemory:
        """Return a port view that can access only ``scope``."""

        return ScopedAgentMemory(store=self, owner_scope=scope)

    def _recall(
        self,
        *,
        scope: AgentMemoryScope,
        query: str | None = None,
        limit: int | None = None,
    ) -> tuple[AgentMemoryRecord, ...]:
        if limit is not None and limit < 1:
            raise ValueError("recall limit must be positive")
        effective_limit = min(
            limit if limit is not None else self._policy.retrieval_limit,
            self._policy.retrieval_limit,
        )
        now = self._clock()
        with self._lock:
            if scope.session_id in self._deleted_sessions:
                return ()
            entries = tuple(
                entry
                for entry in self._entries.get(scope, ())
                if entry.expires_at > now
            )
        if query is not None:
            needle = query.casefold()
            entries = tuple(
                entry for entry in entries if needle in entry.summary.casefold()
            )
        return entries[-effective_limit:]

    def _remember(
        self,
        *,
        scope: AgentMemoryScope,
        memory: AgentMemoryRecord,
    ) -> AgentMemoryRecord:
        if not isinstance(memory, AgentMemoryRecord):
            raise TypeError("memory must be an AgentMemoryRecord")
        now = self._clock()
        if memory.expires_at <= now:
            raise ValueError("cannot remember expired Agent memory")
        ttl_seconds = (memory.expires_at - memory.created_at).total_seconds()
        if ttl_seconds > self._policy.max_ttl_seconds:
            raise ValueError("Agent memory exceeds the maximum TTL")
        with self._lock:
            if scope.session_id in self._deleted_sessions:
                raise RuntimeError(
                    "Agent memory for a deleted session cannot be recreated"
                )
            self._entries.setdefault(scope, []).append(memory)
        return memory

    def _delete_scope(self, *, scope: AgentMemoryScope) -> int:
        with self._lock:
            return len(self._entries.pop(scope, ()))

    def delete_session(self, session_id: str) -> int:
        """Delete and tombstone every Agent namespace for one session."""

        if not isinstance(session_id, str) or not session_id.strip():
            raise ValueError("session_id is required")
        with self._lock:
            matching = tuple(
                scope for scope in self._entries if scope.session_id == session_id
            )
            count = sum(len(self._entries.pop(scope)) for scope in matching)
            self._deleted_sessions.add(session_id)
            return count


class ScopedAgentMemory:
    """AgentMemoryPort adapter bound to one complete ownership scope."""

    def __init__(
        self,
        *,
        store: InMemoryAgentMemoryStore,
        owner_scope: AgentMemoryScope,
    ) -> None:
        self._store = store
        self._owner_scope = owner_scope

    def recall(
        self,
        *,
        scope: AgentMemoryScope,
        query: str | None = None,
        limit: int | None = None,
    ) -> tuple[AgentMemoryRecord, ...]:
        self._require_owner(scope)
        return self._store._recall(scope=scope, query=query, limit=limit)

    def remember(
        self,
        *,
        scope: AgentMemoryScope,
        memory: AgentMemoryRecord,
    ) -> AgentMemoryRecord:
        self._require_owner(scope)
        return self._store._remember(scope=scope, memory=memory)

    def delete_scope(self, *, scope: AgentMemoryScope) -> int:
        self._require_owner(scope)
        return self._store._delete_scope(scope=scope)

    def _require_owner(self, scope: AgentMemoryScope) -> None:
        if scope != self._owner_scope:
            raise AgentMemoryAccessDenied(
                "Agent memory access is restricted to the bound ownership scope"
            )


__all__ = ["InMemoryAgentMemoryStore", "ScopedAgentMemory"]
