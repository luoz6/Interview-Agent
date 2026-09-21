"""Fail-closed execution ownership for Scheduler and historical OLD drain."""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.interview.orchestration_cutover import (
    ExecutionPathBinding,
    ExecutionPathConflict,
    ExecutionPathUnbound,
    OrchestrationPath,
)
from app.ports.execution_path_binding import ExecutionPathBindingPort


@dataclass(frozen=True)
class ExecutionPathRouter:
    binding_store: ExecutionPathBindingPort

    def claim_execution(
        self,
        execution_id: str,
        path: OrchestrationPath,
    ) -> ExecutionPathBinding:
        """Claim an explicit entry path before any orchestration work starts."""

        return self.binding_store.bind(execution_id, path)

    def require_execution_path(
        self,
        execution_id: str,
        expected_path: OrchestrationPath,
    ) -> ExecutionPathBinding:
        binding = self.binding_store.get(execution_id)
        if binding is None:
            raise ExecutionPathUnbound(
                f"execution {execution_id!r} has no orchestration path binding"
            )
        if binding.path != expected_path:
            raise ExecutionPathConflict(
                execution_id=execution_id,
                existing_path=binding.path,
                requested_path=expected_path,
            )
        return binding


__all__ = ["ExecutionPathRouter"]
