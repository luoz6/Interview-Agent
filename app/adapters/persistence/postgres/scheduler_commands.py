"""PostgreSQL adapter for the neutral Scheduler command port."""

from __future__ import annotations

from typing import Any

from app.ports.scheduler_commands import DurableCommandType


class PostgresSchedulerCommandAdapter:
    """Adapt ``PostgresInterviewWorkflowStore`` command persistence."""

    def __init__(self, workflow_store: Any) -> None:
        self.workflow_store = workflow_store

    def enqueue(
        self,
        *,
        execution_id: str,
        command_id: str,
        command_type: DurableCommandType,
        expected_version: int,
        payload: dict[str, Any],
    ) -> Any:
        answer_text = payload.get("answer_text")
        if answer_text is not None and not isinstance(answer_text, str):
            answer_text = str(answer_text)
        return self.workflow_store.enqueue_command(
            session_id=execution_id,
            command_id=command_id,
            command_type=command_type,
            expected_version=expected_version,
            answer_text=answer_text,
        )

    def get(self, *, execution_id: str, command_id: str) -> Any:
        """Read a durable command for replay fencing after process restart."""

        return self.workflow_store.get_command_or_none(execution_id, command_id)

    # Explicit alias for compositions that name the operation after the
    # underlying workflow store method.
    enqueue_command = enqueue


__all__ = ["PostgresSchedulerCommandAdapter"]
