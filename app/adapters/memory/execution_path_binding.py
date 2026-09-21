"""Thread-safe development adapter for orchestration path bindings."""

from __future__ import annotations

from datetime import datetime, timezone
from threading import RLock
from typing import Callable

from app.domain.interview.orchestration_cutover import (
    ExecutionPathBinding,
    ExecutionPathConflict,
    OrchestrationPath,
)


class InMemoryExecutionPathBindingStore:
    def __init__(
        self,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._bindings: dict[str, ExecutionPathBinding] = {}
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._lock = RLock()

    def get(self, execution_id: str) -> ExecutionPathBinding | None:
        with self._lock:
            return self._bindings.get(execution_id)

    def bind(
        self,
        execution_id: str,
        path: OrchestrationPath,
    ) -> ExecutionPathBinding:
        candidate = ExecutionPathBinding(
            execution_id=execution_id,
            path=path,
            bound_at=self._clock(),
        )
        with self._lock:
            existing = self._bindings.get(execution_id)
            if existing is None:
                self._bindings[execution_id] = candidate
                return candidate
            if existing.path != path:
                raise ExecutionPathConflict(
                    execution_id=execution_id,
                    existing_path=existing.path,
                    requested_path=path,
                )
            return existing


__all__ = ["InMemoryExecutionPathBindingStore"]
