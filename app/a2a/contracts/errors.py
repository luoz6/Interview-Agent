from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


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


class A2AAgentError(RuntimeError):
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
        super().__init__(public_message)
        self.code = code
        self.retryable = retryable
        self.terminal = terminal
        self.fallback_allowed = fallback_allowed
        self.public_message = public_message
        self.internal_reason = internal_reason
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
