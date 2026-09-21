from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class AgentError(BaseModel):
    """Transport-neutral error semantics for Agent execution.

    Application and Scheduler code should branch on these four stable fields,
    never on a transport-specific error model.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    error_code: str = Field(min_length=1)
    retryable: bool
    terminal: bool
    fallback_allowed: bool
    public_message: str = Field(min_length=1)
    internal_reason: str = Field(min_length=1)

    @property
    def code(self) -> str:
        """Compatibility spelling for callers that use ``code``."""

        return self.error_code


class AgentFailure(RuntimeError):
    """Neutral runtime exception carrying :class:`AgentError` semantics."""

    def __init__(
        self,
        *,
        error_code: str,
        retryable: bool,
        terminal: bool,
        fallback_allowed: bool,
        public_message: str,
        internal_reason: str,
    ) -> None:
        if not error_code.strip():
            raise ValueError("error_code must be non-empty")
        if not public_message.strip():
            raise ValueError("public_message must be non-empty")
        if not internal_reason.strip():
            raise ValueError("internal_reason must be non-empty")
        super().__init__(public_message)
        self.error_code = error_code
        self.code = error_code
        self.retryable = retryable
        self.terminal = terminal
        self.fallback_allowed = fallback_allowed
        self.public_message = public_message
        self.internal_reason = internal_reason

    def to_error(self) -> AgentError:
        return AgentError(
            error_code=self.error_code,
            retryable=self.retryable,
            terminal=self.terminal,
            fallback_allowed=self.fallback_allowed,
            public_message=self.public_message,
            internal_reason=self.internal_reason,
        )


__all__ = ["AgentError", "AgentFailure"]
