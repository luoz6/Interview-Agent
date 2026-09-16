from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class SessionDeletionJob(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    job_id: str = Field(pattern=r"^delete-[A-Za-z0-9-]{1,128}$")
    session_id: str = Field(min_length=1)
    status: Literal["queued", "running", "completed", "failed"]
    attempt_count: int = Field(default=0, ge=0)
    lease_owner: str | None = None
    lease_token: str | None = None
    lease_expires_at: datetime | None = None
    fencing_version: int = Field(default=0, ge=0)
    error_code: str | None = None
    safe_counts: dict[str, int] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None


__all__ = ["SessionDeletionJob"]
