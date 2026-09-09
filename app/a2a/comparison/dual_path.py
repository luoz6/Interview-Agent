from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.a2a.comparison.comparator import (
    ComparisonResult,
    DeterministicArtifactComparator,
)
from app.a2a.contracts.common import DomainArtifact
from app.a2a.invocation.a2a import A2AAgentInvoker
from app.a2a.invocation.local import LocalAgentInvoker


@dataclass(frozen=True)
class DualPathResult:
    local: DomainArtifact
    a2a: DomainArtifact
    comparison: ComparisonResult


class DualPathRunner:
    def __init__(
        self,
        *,
        local: LocalAgentInvoker,
        a2a: A2AAgentInvoker,
        comparator: DeterministicArtifactComparator | None = None,
    ) -> None:
        self.local = local
        self.a2a = a2a
        self.comparator = comparator or DeterministicArtifactComparator()

    def run(
        self,
        *,
        agent_id: str,
        skill: str,
        request: dict[str, Any],
        execution_context: Any | None = None,
    ) -> DualPathResult:
        local_artifact = self.local.invoke(
            agent_id=agent_id,
            skill=skill,
            request=request,
            execution_context=execution_context,
        )
        a2a_artifact = self.a2a.invoke(
            agent_id=agent_id,
            skill=skill,
            request=request,
            execution_context=execution_context,
        )
        comparison = self.comparator.compare(local_artifact, a2a_artifact)
        return DualPathResult(
            local=local_artifact,
            a2a=a2a_artifact,
            comparison=comparison,
        )
