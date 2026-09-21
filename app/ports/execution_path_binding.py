"""Persistence boundary for immutable orchestration path ownership."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.domain.interview.orchestration_cutover import (
    ExecutionPathBinding,
    OrchestrationPath,
)


@runtime_checkable
class ExecutionPathBindingPort(Protocol):
    def get(self, execution_id: str) -> ExecutionPathBinding | None:
        """Return the existing binding, if any."""

    def bind(
        self,
        execution_id: str,
        path: OrchestrationPath,
    ) -> ExecutionPathBinding:
        """Atomically bind once, replay idempotently, and reject path changes."""


__all__ = ["ExecutionPathBindingPort"]
