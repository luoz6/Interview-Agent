from __future__ import annotations

from datetime import datetime
from hashlib import sha256
import json

from pydantic import BaseModel, ConfigDict, Field


class PrincipalMemoryExportRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = "principal-memory-export-v1"
    export_ref: str = Field(pattern=r"^pm-export-[0-9a-f]{32}$")
    deployment_id: str
    principal_id: str
    payload: dict
    created_at: datetime
    expires_at: datetime


class PrincipalMemoryDeletionTombstone(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = "principal-memory-deletion-tombstone-v1"
    tombstone_ref: str = Field(pattern=r"^pm-delete-[0-9a-f]{64}$")
    deployment_id: str
    principal_id: str
    requested_at: datetime
    completed_at: datetime | None = None
    replayed_at: datetime | None = None
    status: str
    failed_stage: str | None = None
    integrity_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


def _tombstone_digest(*, deployment_id, principal_id, requested_at):
    return sha256(
        json.dumps(
            {
                "deployment_id": deployment_id,
                "principal_id": principal_id,
                "requested_at": requested_at.isoformat(),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


__all__ = [
    "PrincipalMemoryDeletionTombstone",
    "PrincipalMemoryExportRecord",
    "_tombstone_digest",
]
