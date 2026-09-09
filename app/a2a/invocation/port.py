from __future__ import annotations

from typing import Any, Protocol

from app.a2a.contracts.common import DomainArtifact


class AgentInvocationPort(Protocol):
    def invoke(
        self,
        *,
        agent_id: str,
        skill: str,
        request: dict[str, Any],
        execution_context: Any | None = None,
    ) -> DomainArtifact:
        """Invoke a professional Agent skill and return a domain artifact."""
