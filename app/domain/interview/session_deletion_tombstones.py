from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


TOMBSTONE_POLICY_VERSION = "session-deletion-tombstone-v1"


class SessionDeletionTombstone(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["session-deletion-tombstone-v1"] = (
        "session-deletion-tombstone-v1"
    )
    deletion_job_id: str = Field(pattern=r"^delete-[A-Za-z0-9-]{1,128}$")
    session_id: str = Field(min_length=1)
    requested_at: datetime
    completed_at: datetime | None = None
    policy_version: Literal["session-deletion-tombstone-v1"] = (
        TOMBSTONE_POLICY_VERSION
    )
    replay_status: Literal[
        "requested",
        "completed",
        "replayed",
        "failed",
    ] = "requested"
    integrity_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    replayed_at: datetime | None = None
    updated_at: datetime


def tombstone_integrity(
    *,
    deletion_job_id: str,
    session_id: str,
    requested_at: datetime,
    completed_at: datetime | None,
    policy_version: str = TOMBSTONE_POLICY_VERSION,
) -> str:
    payload = {
        "completed_at": completed_at.isoformat() if completed_at else None,
        "deletion_job_id": deletion_job_id,
        "policy_version": policy_version,
        "requested_at": requested_at.isoformat(),
        "session_id": session_id,
    }
    return sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()


def build_tombstone(
    *,
    deletion_job_id: str,
    session_id: str,
    requested_at: datetime,
    completed_at: datetime | None = None,
    replay_status: Literal[
        "requested",
        "completed",
        "replayed",
        "failed",
    ] = "requested",
    replayed_at: datetime | None = None,
    updated_at: datetime | None = None,
) -> SessionDeletionTombstone:
    current = updated_at or datetime.now(timezone.utc)
    return SessionDeletionTombstone(
        deletion_job_id=deletion_job_id,
        session_id=session_id,
        requested_at=requested_at,
        completed_at=completed_at,
        replay_status=replay_status,
        integrity_sha256=tombstone_integrity(
            deletion_job_id=deletion_job_id,
            session_id=session_id,
            requested_at=requested_at,
            completed_at=completed_at,
        ),
        replayed_at=replayed_at,
        updated_at=current,
    )


def validate_tombstone_integrity(tombstone: SessionDeletionTombstone) -> None:
    expected = tombstone_integrity(
        deletion_job_id=tombstone.deletion_job_id,
        session_id=tombstone.session_id,
        requested_at=tombstone.requested_at,
        completed_at=tombstone.completed_at,
        policy_version=tombstone.policy_version,
    )
    if expected != tombstone.integrity_sha256:
        raise ValueError("session deletion tombstone integrity mismatch")


__all__ = [
    "TOMBSTONE_POLICY_VERSION",
    "SessionDeletionTombstone",
    "build_tombstone",
    "tombstone_integrity",
    "validate_tombstone_integrity",
]
