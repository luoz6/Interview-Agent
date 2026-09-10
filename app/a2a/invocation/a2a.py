from __future__ import annotations

from typing import Any

from app.a2a.client import InProcessA2AClient
from app.a2a.contracts.common import DomainArtifact
from app.a2a.invocation.context import InvocationContext
from app.a2a.idempotency import build_agent_idempotency_key


class A2AAgentInvoker:
    def __init__(self, *, client: InProcessA2AClient) -> None:
        self._client = client

    def invoke(
        self,
        *,
        agent_id: str,
        skill: str,
        request: dict[str, Any],
        invocation_context: InvocationContext | None = None,
        execution_context: Any | None = None,
    ) -> DomainArtifact:
        resolved = invocation_context or InvocationContext.from_execution_context(
            execution_context
        )
        resolved_idempotency_key = resolved.idempotency_key or build_agent_idempotency_key(
            agent_id=agent_id,
            skill=skill,
            request=request,
            invocation_context=resolved,
        )
        return self._client.send_task(
            agent_id=agent_id,
            skill=skill,
            request=request,
            execution_context=execution_context,
            context_id=resolved.context_id,
            correlation_id=resolved.correlation_id,
            causation_id=resolved.causation_id,
            parent_run_id=resolved.parent_run_id,
            command_id=resolved.command_id,
            idempotency_key=resolved_idempotency_key,
        )
