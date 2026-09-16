from __future__ import annotations

from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.interview.decision_store import DecisionContract


class FollowupDecisionProvider(Protocol):
    def __call__(self, context: dict[str, object]) -> object: ...


class DecisionProviderResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    decision: DecisionContract
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cached_input_tokens: int | None = Field(default=None, ge=0)
    provider_model: str | None = None
    provider_response_id: str | None = None

    @model_validator(mode="after")
    def validate_cached_usage(self):
        if (
            self.cached_input_tokens is not None
            and self.input_tokens is not None
            and self.cached_input_tokens > self.input_tokens
        ):
            raise ValueError("cached input tokens cannot exceed input tokens")
        return self


class ProviderModelMismatchError(ValueError):
    """A provider response identified a model outside exact authorization."""


class DecisionExecutionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["accepted", "completed"]
    decision_id: str
    attempt_number: int | None
    decision: DecisionContract | None
    replayed: bool
    provider_invocations: int = Field(ge=0)
    duration_ms: float = Field(ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cached_input_tokens: int | None = Field(default=None, ge=0)
    provider_response_id_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
