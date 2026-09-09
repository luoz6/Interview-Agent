from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.a2a.contracts.common import DomainArtifact
from app.a2a.contracts.errors import A2AAgentError
from app.a2a.invocation.context import InvocationContext
from app.a2a.invocation.execution_context import build_agent_execution_context


LocalSkillHandler = Callable[[dict[str, Any], Any | None], DomainArtifact]


class LocalAgentInvoker:
    """Registry-backed local implementation of AgentInvocationPort.

    Business handlers are registered explicitly during A2A migration so that
    existing direct calls can be migrated one Agent/skill at a time.
    """

    def __init__(self) -> None:
        self._handlers: dict[tuple[str, str], LocalSkillHandler] = {}

    def register(
        self,
        *,
        agent_id: str,
        skill: str,
        handler: LocalSkillHandler,
    ) -> None:
        self._handlers[(agent_id, skill)] = handler

    def invoke(
        self,
        *,
        agent_id: str,
        skill: str,
        request: dict[str, Any],
        invocation_context: InvocationContext | None = None,
        execution_context: Any | None = None,
    ) -> DomainArtifact:
        handler = self._handlers.get((agent_id, skill))
        if handler is None:
            raise A2AAgentError(
                code="unsupported_skill",
                retryable=False,
                terminal=True,
                fallback_allowed=False,
                public_message="Agent skill is not available.",
                internal_reason=f"{agent_id}:{skill} is not registered locally",
            )
        resolved_execution_context = execution_context or build_agent_execution_context(
            agent_id=agent_id,
            skill=skill,
            invocation_context=invocation_context,
            request=request,
        )
        return handler(request, resolved_execution_context)
