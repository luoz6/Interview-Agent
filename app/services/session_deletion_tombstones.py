from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from threading import RLock
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


class InMemorySessionDeletionTombstoneStore:
    def __init__(self, *, clock=None) -> None:
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._lock = RLock()
        self._items: dict[str, SessionDeletionTombstone] = {}

    def record_requested(self, job) -> SessionDeletionTombstone:
        with self._lock:
            existing = self._items.get(job.session_id)
            if existing is not None:
                return existing
            item = build_tombstone(
                deletion_job_id=job.job_id,
                session_id=job.session_id,
                requested_at=job.created_at,
                updated_at=self.clock(),
            )
            self._items[job.session_id] = item
            return item

    def record_completed(self, job) -> SessionDeletionTombstone:
        with self._lock:
            existing = self._items.get(job.session_id)
            requested_at = existing.requested_at if existing else job.created_at
            item = build_tombstone(
                deletion_job_id=job.job_id,
                session_id=job.session_id,
                requested_at=requested_at,
                completed_at=job.completed_at or self.clock(),
                replay_status="completed",
                updated_at=self.clock(),
            )
            self._items[job.session_id] = item
            return item

    def mark_replayed(
        self,
        tombstone: SessionDeletionTombstone,
    ) -> SessionDeletionTombstone:
        validate_tombstone_integrity(tombstone)
        with self._lock:
            item = tombstone.model_copy(
                update={
                    "replay_status": "replayed",
                    "replayed_at": self.clock(),
                    "updated_at": self.clock(),
                }
            )
            self._items[item.session_id] = item
            return item

    def get_for_session(
        self, session_id: str
    ) -> SessionDeletionTombstone | None:
        with self._lock:
            return self._items.get(session_id)

    def list_completed(self, *, limit: int = 1000) -> list[SessionDeletionTombstone]:
        if limit < 1 or limit > 10_000:
            raise ValueError("tombstone limit is out of range")
        with self._lock:
            return [
                item
                for item in sorted(
                    self._items.values(),
                    key=lambda value: (value.requested_at, value.deletion_job_id),
                )
                if item.replay_status in {"completed", "replayed"}
            ][:limit]
