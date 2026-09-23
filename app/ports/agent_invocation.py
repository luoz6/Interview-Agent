"""Canonical neutral Agent invocation boundary.

Adapters (local, A2A, or another transport) implement this Protocol.  The
Protocol intentionally depends only on domain contracts and never on a
transport package.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, _ProtocolMeta

from app.domain.agent_execution import AgentExecutionContext
from app.domain.agents.artifacts import DomainArtifact
from app.domain.interview.scheduling.requests import AgentRequest


class _AgentInvocationPortMeta(_ProtocolMeta):
    def __instancecheck__(cls, instance):
        if cls.__name__ == "AgentInvocationPort":
            return callable(getattr(instance, "invoke", None))
        return super().__instancecheck__(instance)


class AgentInvocationPort(Protocol, metaclass=_AgentInvocationPortMeta):
    """Structural sync port with an optional streaming extension.

    Existing synchronous adapters remain valid; streaming-capable adapters
    additionally implement ``invoke_stream``.
    """

    def invoke(
        self,
        *,
        agent_id: str,
        skill: str,
        request: AgentRequest,
        execution_context: AgentExecutionContext | None = None,
    ) -> DomainArtifact:
        """Invoke one typed Agent request and return a neutral artifact."""

    def invoke_stream(
        self,
        *,
        agent_id: str,
        skill: str,
        request: AgentRequest,
        on_delta: Callable[[str], None],
        execution_context: AgentExecutionContext | None = None,
    ) -> DomainArtifact:
        """Stream provider chunks, then return one canonical artifact."""


__all__ = ["AgentInvocationPort"]
