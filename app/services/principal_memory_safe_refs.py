from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import RLock
from uuid import uuid4


class PrincipalMemorySafeRefInvalid(ValueError):
    pass


class PrincipalMemorySafeRefVersionConflict(PrincipalMemorySafeRefInvalid):
    pass


@dataclass(frozen=True)
class PrincipalMemorySafeRefRecord:
    safe_ref: str
    deployment_id: str
    principal_id: str
    fact_id: str
    fact_version: int
    expires_at: datetime


class InMemoryPrincipalMemorySafeRefStore:
    def __init__(self, *, clock=None, ref_factory=None, ttl_seconds=900):
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.ref_factory = ref_factory or (lambda: f"pm-ref-{uuid4().hex}")
        self.ttl_seconds = ttl_seconds
        self._items = {}
        self._lock = RLock()

    def issue(self, fact):
        now = self.clock()
        with self._lock:
            existing = next(
                (
                    item
                    for item in self._items.values()
                    if item.deployment_id == fact.deployment_id
                    and item.principal_id == fact.principal_id
                    and item.fact_id == fact.fact_id
                    and item.fact_version == fact.version
                    and item.expires_at > now
                ),
                None,
            )
            if existing is not None:
                return existing.safe_ref
        record = PrincipalMemorySafeRefRecord(
            safe_ref=self.ref_factory(),
            deployment_id=fact.deployment_id,
            principal_id=fact.principal_id,
            fact_id=fact.fact_id,
            fact_version=fact.version,
            expires_at=now + timedelta(seconds=self.ttl_seconds),
        )
        with self._lock:
            self._items[record.safe_ref] = record
        return record.safe_ref

    def resolve(self, safe_ref, *, deployment_id, principal_id, fact_store):
        now = self.clock()
        with self._lock:
            record = self._items.get(safe_ref)
        if (
            record is None
            or record.expires_at <= now
            or record.deployment_id != deployment_id
            or record.principal_id != principal_id
        ):
            raise PrincipalMemorySafeRefInvalid(
                "principal memory safe reference is stale"
            )
        fact = fact_store.get(
            deployment_id=deployment_id,
            principal_id=principal_id,
            fact_id=record.fact_id,
        )
        if fact is None:
            raise PrincipalMemorySafeRefInvalid(
                "principal memory safe reference is stale"
            )
        if fact.version != record.fact_version:
            raise PrincipalMemorySafeRefVersionConflict(
                "principal memory safe reference is stale because its version changed"
            )
        return fact

    def purge(self, *, deployment_id, principal_id):
        with self._lock:
            keys = [
                key
                for key, record in self._items.items()
                if record.deployment_id == deployment_id
                and record.principal_id == principal_id
            ]
            for key in keys:
                del self._items[key]
            return len(keys)

    def count(self, *, deployment_id, principal_id):
        with self._lock:
            return sum(
                item.deployment_id == deployment_id
                and item.principal_id == principal_id
                for item in self._items.values()
            )

    def cleanup_expired(self, *, now=None, batch_size=200):
        now = now or self.clock()
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
