from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator


AgentStreamEventType = Literal["STARTED", "DELTA", "COMPLETED", "FAILED"]


class AgentStreamIdentity(BaseModel):
    """Stable identity for one logical Agent invocation attempt."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    execution_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    logical_attempt: int = Field(ge=1)
    agent_id: str = Field(min_length=1)
    skill: str = Field(min_length=1)
    question_id: str | None = None

    @computed_field
    @property
    def stream_id(self) -> str:
        material = json.dumps(
            [self.execution_id, self.task_id, self.logical_attempt],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        return f"agent-stream/sha256:{hashlib.sha256(material).hexdigest()}"


class AgentStreamEvent(BaseModel):
    """Frozen observer envelope for one Agent streaming attempt."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["agent-stream-event-v1"] = "agent-stream-event-v1"
    event_type: AgentStreamEventType
    execution_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    logical_attempt: int = Field(ge=1)
    agent_id: str = Field(min_length=1)
    skill: str = Field(min_length=1)
    stream_id: str = Field(min_length=1)
    sequence: int = Field(ge=1)
    event_id: str = Field(min_length=1)
    question_id: str | None = None
    delta: str | None = None
    final_text: str | None = None
    artifact_ref: str | None = None
    error_code: str | None = None
    retryable: bool | None = None
    emitted_at: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_event_contract(self) -> "AgentStreamEvent":
        expected_event_id = f"{self.stream_id}:{self.sequence}"
        if self.event_id != expected_event_id:
            raise ValueError("event_id must equal {stream_id}:{sequence}")
        if self.event_type == "DELTA" and not self.delta:
            raise ValueError("DELTA requires delta")
        if self.event_type == "COMPLETED":
            if self.final_text is None:
                raise ValueError("COMPLETED requires final_text")
            if not self.artifact_ref:
                raise ValueError("COMPLETED requires artifact_ref")
        if self.event_type == "FAILED":
            if not self.error_code:
                raise ValueError("FAILED requires error_code")
            if self.retryable is None:
                raise ValueError("FAILED requires retryable")
        return self


class CommittedStreamArtifact(BaseModel):
    """Canonical text and durable reference returned by the commit owner."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    artifact_ref: str = Field(min_length=1)
    final_text: str


__all__ = [
    "AgentStreamEvent",
    "AgentStreamEventType",
    "AgentStreamIdentity",
    "CommittedStreamArtifact",
]
