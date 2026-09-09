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
    ) -> DomainArtifact:
        return self._client.send_task(
            agent_id=agent_id,
            skill=skill,
            request=request,
            execution_context=execution_context,
        )
