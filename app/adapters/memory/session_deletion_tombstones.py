from __future__ import annotations

from datetime import datetime, timezone
from threading import RLock

from app.domain.interview.session_deletion_tombstones import (
    SessionDeletionTombstone,
    build_tombstone,
    validate_tombstone_integrity,
)


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


__all__ = ["InMemorySessionDeletionTombstoneStore"]
