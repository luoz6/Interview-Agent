from __future__ import annotations

from threading import RLock

from app.domain.interview.scheduling import (
    ExecutionPlan,
    ExecutionState,
    ExecutionStateConflict,
)


class InMemorySchedulerExecutionRepository:
    def __init__(self) -> None:
        self._plans: dict[str, ExecutionPlan] = {}
        self._states: dict[str, ExecutionState] = {}
        self._deleted: set[str] = set()
        self._lock = RLock()

    def create(self, plan: ExecutionPlan, state: ExecutionState) -> None:
        if plan.execution_id != state.execution_id:
            raise ValueError("plan and state execution identities differ")
        execution_id = plan.execution_id
        with self._lock:
            if execution_id in self._deleted:
                raise RuntimeError("deleted Scheduler execution cannot be recreated")
            existing_plan = self._plans.get(execution_id)
            existing_state = self._states.get(execution_id)
            if existing_plan is not None or existing_state is not None:
                if existing_plan != plan or existing_state != state:
                    raise RuntimeError("Scheduler execution already exists with other data")
                return
            self._plans[execution_id] = plan
            self._states[execution_id] = state

    def load_plan(self, execution_id: str) -> ExecutionPlan:
        with self._lock:
            try:
                return self._plans[execution_id]
            except KeyError as exc:
                raise KeyError(f"execution plan not found: {execution_id}") from exc

    def load(self, execution_id: str) -> ExecutionState:
        with self._lock:
            try:
                return self._states[execution_id]
            except KeyError as exc:
                raise KeyError(f"execution state not found: {execution_id}") from exc

    def save(self, state: ExecutionState) -> ExecutionState:
        with self._lock:
            current = self._states.get(state.execution_id)
            if current is None:
                raise KeyError(f"execution state not found: {state.execution_id}")
            if state == current:
                return state
            if state.revision <= current.revision:
                raise ExecutionStateConflict(
                    expected_revision=state.revision,
                    actual_revision=current.revision,
                )
            self._states[state.execution_id] = state
            return state

    def delete(self, execution_id: str) -> int:
        with self._lock:
            existed = execution_id in self._plans or execution_id in self._states
            self._plans.pop(execution_id, None)
            self._states.pop(execution_id, None)
            self._deleted.add(execution_id)
            return int(existed)


__all__ = ["InMemorySchedulerExecutionRepository"]
