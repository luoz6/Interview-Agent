from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.domain.agents.errors import AgentError, AgentFailure

A2AErrorCode = Literal[
    "protocol_error",
    "unsupported_skill",
    "invalid_request",
    "task_rejected",
    "domain_validation_failed",
    "provider_timeout",
    "provider_unavailable",
    "knowledge_unavailable",
    "artifact_validation_failed",
    "state_conflict",
    "lease_lost",
    "task_canceled",
    "unexpected_error",
]


class A2AError(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    code: A2AErrorCode
    retryable: bool
    terminal: bool
    fallback_allowed: bool
    public_message: str
    internal_reason: str
    observability_code: str

    def to_neutral_error(self) -> AgentError:
        """Convert the wire error into the core Agent error contract."""

        return AgentError(
            error_code=self.code,
            retryable=self.retryable,
            terminal=self.terminal,
            fallback_allowed=self.fallback_allowed,
            public_message=self.public_message,
            internal_reason=self.internal_reason,
        )


class A2AAgentError(AgentFailure):
    """Runtime exception carrying a stable A2A error code."""

    def __init__(
        self,
        *,
        code: A2AErrorCode,
        retryable: bool,
        terminal: bool,
        fallback_allowed: bool,
        public_message: str,
        internal_reason: str,
        observability_code: str | None = None,
    ) -> None:
        super().__init__(
            error_code=code,
            retryable=retryable,
            terminal=terminal,
            fallback_allowed=fallback_allowed,
            public_message=public_message,
            internal_reason=internal_reason,
        )
        self.code = code
        self.observability_code = observability_code or code

    def to_artifact(self) -> A2AError:
        return A2AError(
            code=self.code,
            retryable=self.retryable,
            terminal=self.terminal,
            fallback_allowed=self.fallback_allowed,
            public_message=self.public_message,
            internal_reason=self.internal_reason,
            observability_code=self.observability_code,
        )

    def to_neutral_error(self) -> AgentError:
        return self.to_error()


def to_neutral_error(error: A2AError | A2AAgentError) -> AgentError:
    """Adapter boundary from A2A errors to the neutral core contract."""

    return error.to_neutral_error()
