from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.domain.interview.scheduling import ExecutionPlan, ExecutionState


@runtime_checkable
class SchedulerExecutionRepository(Protocol):
    """Durable source of Scheduler definitions and canonical runtime state."""

    def create(self, plan: ExecutionPlan, state: ExecutionState) -> None: ...

    def load_plan(self, execution_id: str) -> ExecutionPlan: ...

    def load(self, execution_id: str) -> ExecutionState: ...

    def save(self, state: ExecutionState) -> ExecutionState: ...

    def delete(self, execution_id: str) -> int: ...


__all__ = ["SchedulerExecutionRepository"]
