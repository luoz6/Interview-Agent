from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PrincipalMemoryControlConflict(RuntimeError):
    """Raised when a stale control version attempts to overwrite newer intent."""


class PrincipalMemoryControl(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["principal-memory-control-v1"] = (
        "principal-memory-control-v1"
    )
    deployment_id: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,128}$")
    principal_id: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,128}$")
    scope: Literal["global", "session"]
    session_id: str | None = Field(default=None, min_length=1, max_length=128)
    enabled: bool
    updated_at: datetime
    version: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def validate_scope(self):
        if (self.scope == "global") != (self.session_id is None):
            raise ValueError("principal memory control scope conflicts with session")
        if self.updated_at.tzinfo is None:
            raise ValueError("principal memory control timestamp must be timezone-aware")
        return self


__all__ = ["PrincipalMemoryControl", "PrincipalMemoryControlConflict"]
