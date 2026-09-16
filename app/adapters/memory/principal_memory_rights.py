from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from threading import RLock

from app.domain.memory.rights import (
    PrincipalMemoryDeletionTombstone,
    PrincipalMemoryExportRecord,
    _tombstone_digest,
)


class InMemoryPrincipalMemoryExportStore:
    def __init__(self):
        self._items = {}
        self._lock = RLock()

    def put(self, record: PrincipalMemoryExportRecord):
        with self._lock:
            self._items[record.export_ref] = record
        return record

    def get(self, export_ref: str, *, now):
        with self._lock:
            record = self._items.get(export_ref)
            if record is None or record.expires_at <= now:
                return None
            return record

    def purge(self, *, deployment_id: str, principal_id: str) -> int:
        with self._lock:
            keys = [
                key
                for key, item in self._items.items()
                if item.deployment_id == deployment_id
                and item.principal_id == principal_id
            ]
            for key in keys:
                del self._items[key]
            return len(keys)

    def count(self, *, deployment_id: str, principal_id: str) -> int:
        with self._lock:
            return sum(
                item.deployment_id == deployment_id
                and item.principal_id == principal_id
                for item in self._items.values()
            )

    def cleanup_expired(self, *, now: datetime, batch_size: int = 200) -> int:
        if now.tzinfo is None:
            raise ValueError("principal memory cleanup time must be timezone-aware")
        if batch_size < 1:
            raise ValueError("principal memory cleanup batch size must be positive")
        with self._lock:
            keys = [
                key
                for key, item in sorted(self._items.items())
                if item.expires_at <= now
            ][:batch_size]
            for key in keys:
                del self._items[key]
            return len(keys)


class InMemoryPrincipalMemoryDeletionTombstoneStore:
    def __init__(self, *, clock=None):
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._items = {}
        self._latest = {}
        self._lock = RLock()

    def record_requested(self, *, deployment_id, principal_id):
        with self._lock:
            key = (deployment_id, principal_id)
            existing_ref = self._latest.get(key)
            existing = self._items.get(existing_ref) if existing_ref else None
            if existing is not None and existing.status in {"requested", "failed"}:
                return existing
            now = self.clock()
            digest = _tombstone_digest(
                deployment_id=deployment_id,
                principal_id=principal_id,
                requested_at=now,
            )
            item = PrincipalMemoryDeletionTombstone(
                tombstone_ref=f"pm-delete-{digest}",
                deployment_id=deployment_id,
                principal_id=principal_id,
                requested_at=now,
                status="requested",
                integrity_sha256=digest,
            )
            self._items[item.tombstone_ref] = item
            self._latest[key] = item.tombstone_ref
            return item

    def completion_candidate(self, tombstone):
        self.validate(tombstone)
        completed_at = self.clock()
        if completed_at.tzinfo is None or completed_at.utcoffset() is None:
            raise ValueError("principal deletion completion time must be timezone-aware")
        return tombstone.model_copy(
            update={
                "status": "completed",
                "failed_stage": None,
                "completed_at": completed_at,
            }
        )

    def mark(self, tombstone, *, status, failed_stage=None, completed_at=None):
        self.validate(tombstone)
        now = self.clock()
        selected_completed_at = completed_at or now
        if status == "completed" and (
            selected_completed_at.tzinfo is None
            or selected_completed_at.utcoffset() is None
        ):
            raise ValueError("principal deletion completion time must be timezone-aware")
        item = tombstone.model_copy(
            update={
                "status": status,
                "failed_stage": failed_stage,
                "completed_at": (
                    selected_completed_at
                    if status == "completed"
                    else tombstone.completed_at
                ),
                "replayed_at": now if status == "replayed" else tombstone.replayed_at,
            }
        )
        with self._lock:
            if tombstone.tombstone_ref not in self._items:
                raise RuntimeError("principal deletion tombstone changed")
            self._items[item.tombstone_ref] = item
            key = (item.deployment_id, item.principal_id)
            latest_ref = self._latest.get(key)
            latest = self._items.get(latest_ref) if latest_ref else None
            if latest is None or (item.requested_at, item.tombstone_ref) >= (
                latest.requested_at,
                latest.tombstone_ref,
            ):
                self._latest[key] = item.tombstone_ref
        return item

    def import_tombstone(self, tombstone):
        self.validate(tombstone)
        with self._lock:
            existing = self._items.get(tombstone.tombstone_ref)
            if existing is not None and (
                existing.integrity_sha256 != tombstone.integrity_sha256
            ):
                raise RuntimeError("principal deletion tombstone conflict")
            if existing is None:
                self._items[tombstone.tombstone_ref] = tombstone
            key = (tombstone.deployment_id, tombstone.principal_id)
            latest_ref = self._latest.get(key)
            latest = self._items.get(latest_ref) if latest_ref else None
            if latest is None or (
                tombstone.requested_at,
                tombstone.tombstone_ref,
            ) >= (latest.requested_at, latest.tombstone_ref):
                self._latest[key] = tombstone.tombstone_ref
            if existing is None:
                return tombstone
            return existing

    @staticmethod
    def validate(tombstone):
        expected = _tombstone_digest(
            deployment_id=tombstone.deployment_id,
            principal_id=tombstone.principal_id,
            requested_at=tombstone.requested_at,
        )
        if expected != tombstone.integrity_sha256:
            raise ValueError("principal deletion tombstone integrity mismatch")

    def get(self, *, deployment_id, principal_id):
        with self._lock:
            ref = self._latest.get((deployment_id, principal_id))
            return self._items.get(ref) if ref else None

    def is_write_blocked(self, *, deployment_id, principal_id) -> bool:
        current = self.get(deployment_id=deployment_id, principal_id=principal_id)
        return bool(current and current.status in {"requested", "failed"})

    @contextmanager
    def writer_guard(self, *, deployment_id, principal_id):
        key = (deployment_id, principal_id)
        observed_ref = self._latest.get(key)
        observed = self._items.get(observed_ref) if observed_ref else None
        observed_state = (
            (observed.tombstone_ref, observed.status) if observed else None
        )
        with self._lock:
            current = self.get(
                deployment_id=deployment_id,
                principal_id=principal_id,
            )
            current_state = (
                (current.tombstone_ref, current.status) if current else None
            )
            if current_state != observed_state or (
                current and current.status in {"requested", "failed"}
            ):
                raise PermissionError("principal memory deletion fence is active")
            yield

    @contextmanager
    def deletion_guard(self, *, deployment_id, principal_id):
        del deployment_id, principal_id
        with self._lock:
            yield


__all__ = [
    "InMemoryPrincipalMemoryDeletionTombstoneStore",
    "InMemoryPrincipalMemoryExportStore",
]
