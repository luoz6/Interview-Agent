"""Canonical neutral Agent invocation boundary.

Adapters (local, A2A, or another transport) implement this Protocol.  The
Protocol intentionally depends only on domain contracts and never on a
transport package.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.domain.agent_execution import AgentExecutionContext
from app.domain.agents.artifacts import DomainArtifact
from app.domain.interview.scheduling.requests import AgentRequest


@runtime_checkable
class AgentInvocationPort(Protocol):
    def invoke(
        self,
        *,
        agent_id: str,
        skill: str,
        request: AgentRequest,
        execution_context: AgentExecutionContext | None = None,
    ) -> DomainArtifact:
        """Invoke one typed Agent request and return a neutral artifact."""


__all__ = ["AgentInvocationPort"]
