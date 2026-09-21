from __future__ import annotations

from typing import Any, Mapping

from app.a2a.client import InProcessA2AClient
from app.domain.agents.artifacts import DomainArtifact
from app.domain.agent_execution import AgentExecutionContext
from app.domain.interview.scheduling.requests import AgentRequest
from app.ports.agent_invocation import AgentInvocationPort
from app.a2a.invocation.context import InvocationContext
from app.a2a.idempotency import build_agent_idempotency_key


class A2AAgentInvoker:
    """A2A transport adapter implementing the canonical neutral Port."""

    # The explicit alias keeps the adapter's implementation relationship
    # inspectable without making A2A transport types part of the Port.
    port_type = AgentInvocationPort

    def __init__(self, *, client: InProcessA2AClient) -> None:
        self._client = client

    def invoke(
        self,
        *,
        agent_id: str,
        skill: str,
        request: AgentRequest,
        invocation_context: InvocationContext | None = None,
        execution_context: AgentExecutionContext | None = None,
    ) -> DomainArtifact:
        serialized_request: dict[str, Any]
        if isinstance(request, AgentRequest):
            serialized_request = request.model_dump(mode="python")
        elif isinstance(request, Mapping):
            # Compatibility for pre-T11 callers. New core callers must pass a
            # typed AgentRequest; dicts are serialized only at this adapter.
            serialized_request = dict(request)
        else:
            raise TypeError("A2A invocation request must be an AgentRequest")
        resolved = invocation_context or InvocationContext.from_execution_context(
            execution_context
        )
        resolved_idempotency_key = resolved.idempotency_key or build_agent_idempotency_key(
            agent_id=agent_id,
            skill=skill,
            request=serialized_request,
            invocation_context=resolved,
        )
        return self._client.send_task(
            agent_id=agent_id,
            skill=skill,
            request=serialized_request,
            execution_context=execution_context,
            context_id=resolved.context_id,
            correlation_id=resolved.correlation_id,
            causation_id=resolved.causation_id,
            parent_run_id=resolved.parent_run_id,
            command_id=resolved.command_id,
            idempotency_key=resolved_idempotency_key,
        )
