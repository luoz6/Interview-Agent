"""Neutral port for an Agent's session-local private memory."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.domain.memory.agent import AgentMemoryRecord, AgentMemoryScope


@runtime_checkable
class AgentMemoryPort(Protocol):
    """Minimal memory boundary shared by Agent implementations and adapters.

    The concrete scope/ownership value is deliberately supplied by the caller;
    MA6-T04 defines the immutable namespace that will be used there.  Keeping
    this port transport- and storage-neutral lets the existing memory
    infrastructure back it without creating a second memory subsystem.
    """

    def recall(
        self,
        *,
        scope: AgentMemoryScope,
        query: str | None = None,
        limit: int | None = None,
    ) -> tuple[AgentMemoryRecord, ...]:
        """Return memory entries visible in one Agent scope."""

    def remember(
        self,
        *,
        scope: AgentMemoryScope,
        memory: AgentMemoryRecord,
    ) -> AgentMemoryRecord:
        """Store one memory entry in the supplied Agent scope."""

    def delete_scope(self, *, scope: AgentMemoryScope) -> int:
        """Delete every memory entry in the supplied scope and return its count."""


__all__ = ["AgentMemoryPort"]
