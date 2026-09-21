"""Durable command enqueue boundary for the deterministic Scheduler.

The application layer depends on this small neutral protocol rather than on a
database adapter (or an A2A transport).  Adapters preserve the existing
``command_id``/``expected_version`` fencing semantics at their boundary.
"""

from __future__ import annotations

from typing import Any, Literal, Protocol, runtime_checkable


DurableCommandType = Literal["answer", "skip", "finish"]


@runtime_checkable
class SchedulerCommandPort(Protocol):
    """Enqueue a user command durably and idempotently."""

    def enqueue(
        self,
        *,
        execution_id: str,
        command_id: str,
        command_type: DurableCommandType,
        expected_version: int,
        payload: dict[str, Any],
    ) -> Any:
        """Persist a command using the runtime's durable command semantics.

        Implementations should return the existing record for an identical
        replay and raise a payload-conflict error for a reused id with a
        different payload.
        """


__all__ = [
    "DurableCommandType",
    "SchedulerCommandPort",
]
