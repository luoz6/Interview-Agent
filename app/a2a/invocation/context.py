from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.services.agent_runtime import AgentExecutionContext


class InvocationContext(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    context_id: str | None = None
    correlation_id: str | None = None
    causation_id: str | None = None
    parent_run_id: str | None = None
    command_id: str | None = None
    session_id: str | None = None
    question_id: str | None = None
    state_version: int | None = Field(default=None, ge=1)
    evidence_ids: list[str] = Field(default_factory=list)
    idempotency_key: str | None = None

    @classmethod
    def from_execution_context(
        cls,
        context: AgentExecutionContext | None,
    ) -> "InvocationContext":
        if context is None:
            return cls()
        return cls(
            context_id=context.correlation_id,
            correlation_id=context.correlation_id,
            causation_id=context.causation_id,
            parent_run_id=context.parent_run_id,
            command_id=context.command_id,
            session_id=context.session_id,
            question_id=context.question_id,
            state_version=context.state_version,
            evidence_ids=list(context.evidence_ids),
        )
