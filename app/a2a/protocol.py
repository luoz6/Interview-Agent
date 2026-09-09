from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.a2a.contracts.common import DomainArtifact
from app.a2a.contracts.errors import A2AError


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class A2AMessagePart(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["text", "data"] = "data"
    content: Any


class A2AMessage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    message_id: str = Field(min_length=1)
    from_agent: str = Field(min_length=1)
    to_agent: str = Field(min_length=1)
    context_id: str = Field(min_length=1)
    correlation_id: str | None = None
    task_id: str | None = None
    parts: list[A2AMessagePart] = Field(default_factory=list)


class A2ATask(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    task_id: str = Field(min_length=1)
    agent_id: str = Field(min_length=1)
    skill: str = Field(min_length=1)
    status: Literal[
        "submitted",
        "working",
        "input-required",
        "completed",
        "failed",
        "canceled",
        "rejected",
    ] = "submitted"
    input: dict[str, Any] = Field(default_factory=dict)
    output_artifact: DomainArtifact | None = None
    error: A2AError | None = None
    attempts: int = Field(default=0, ge=0)
    created_at: str = Field(default_factory=_utc_now_iso)
    updated_at: str = Field(default_factory=_utc_now_iso)
    context_id: str | None = None
    correlation_id: str | None = None
    causation_id: str | None = None
    parent_run_id: str | None = None
    command_id: str | None = None


class A2AResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    task: A2ATask
    message: A2AMessage | None = None
