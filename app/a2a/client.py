from __future__ import annotations

from typing import Any
from uuid import uuid4

from app.a2a.contracts.common import DomainArtifact
from app.a2a.contracts.errors import A2AAgentError
from app.a2a.protocol import A2ATask
from app.a2a.server import LocalA2AServer


class InProcessA2AClient:
    def __init__(self, *, server: LocalA2AServer) -> None:
        self.server = server

    def send_task(
        self,
        *,
        agent_id: str,
        skill: str,
        request: dict[str, Any],
        execution_context: Any | None = None,
        task_id: str | None = None,
        context_id: str | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        parent_run_id: str | None = None,
        command_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> DomainArtifact:
        task = A2ATask(
            task_id=task_id or f"task-{uuid4().hex}",
            agent_id=agent_id,
            skill=skill,
            input=request,
            context_id=context_id,
            correlation_id=correlation_id,
            causation_id=causation_id,
            parent_run_id=parent_run_id,
            command_id=command_id,
            idempotency_key=idempotency_key,
        )
        result = self.server.submit(task, execution_context=execution_context)
        completed = result.task
        if completed.status == "completed" and completed.output_artifact is not None:
            return completed.output_artifact
        error = completed.error
        if error is None:
            raise A2AAgentError(
                code="protocol_error",
                retryable=False,
                terminal=True,
                fallback_allowed=False,
                public_message="Agent task did not complete.",
                internal_reason=f"{agent_id}:{skill} returned {completed.status}",
            )
        raise A2AAgentError(
            code=error.code,
            retryable=error.retryable,
            terminal=error.terminal,
            fallback_allowed=error.fallback_allowed,
            public_message=error.public_message,
            internal_reason=error.internal_reason,
            observability_code=error.observability_code,
        )
