"""Identity and ownership contracts for Agent-private memory."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


AgentMemoryType = Literal[
    "scheduler",
    "knowledge",
    "examiner",
    "reviewer",
    "report-coach",
]

AGENT_MEMORY_TYPES: tuple[AgentMemoryType, ...] = (
    "scheduler",
    "knowledge",
    "examiner",
    "reviewer",
    "report-coach",
)


class AgentMemoryAccessDenied(PermissionError):
    """Raised when a scoped Agent memory port receives a foreign scope."""


class AgentMemoryRecord(BaseModel):
    """Bounded summary retained in one session-local Agent namespace."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["agent-memory-v1"] = "agent-memory-v1"
    memory_id: str = Field(pattern=r"^[A-Za-z0-9_.:-]{1,128}$")
    summary: str = Field(min_length=1, max_length=4096)
    created_at: datetime
    expires_at: datetime
    source_artifact_refs: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_lifetime(self):
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("created_at must be timezone-aware")
        if self.expires_at.tzinfo is None or self.expires_at.utcoffset() is None:
            raise ValueError("expires_at must be timezone-aware")
        if self.expires_at <= self.created_at:
            raise ValueError("expires_at must be after created_at")
        if len(self.source_artifact_refs) != len(set(self.source_artifact_refs)):
            raise ValueError("source artifact refs must be unique")
        return self


class AgentMemoryScope(BaseModel):
    """Complete ownership key for one Agent-private memory namespace.

    All dimensions are required so an adapter cannot accidentally scope memory
    by agent ID alone. ``memory_type`` selects one of the five logical
    namespaces while all namespaces continue to use the same memory port.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    deployment_id: str = Field(min_length=1, max_length=128)
    principal_id: str = Field(min_length=1, max_length=128)
    session_id: str = Field(min_length=1, max_length=128)
    agent_id: str = Field(min_length=1, max_length=128)
    memory_type: AgentMemoryType


__all__ = [
    "AGENT_MEMORY_TYPES",
    "AgentMemoryAccessDenied",
    "AgentMemoryRecord",
    "AgentMemoryScope",
    "AgentMemoryType",
]
