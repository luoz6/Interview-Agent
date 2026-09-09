from __future__ import annotations

from typing import Any

from app.a2a.client import InProcessA2AClient
from app.a2a.contracts.common import DomainArtifact


class A2AAgentInvoker:
    def __init__(self, *, client: InProcessA2AClient) -> None:
        self._client = client

    def invoke(
        self,
        *,
        agent_id: str,
        skill: str,
        request: dict[str, Any],
        execution_context: Any | None = None,
        context_id: str | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        parent_run_id: str | None = None,
        command_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> DomainArtifact:
        return self._client.send_task(
            agent_id=agent_id,
            skill=skill,
            request=request,
            execution_context=execution_context,
            context_id=context_id,
            correlation_id=correlation_id,
            causation_id=causation_id,
            parent_run_id=parent_run_id,
            command_id=command_id,
            idempotency_key=idempotency_key,
        )
