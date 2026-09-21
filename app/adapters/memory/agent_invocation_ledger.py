"""In-memory Agent invocation ledger for development and crash-window tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from threading import RLock
from typing import Callable
from uuid import uuid4

from app.domain.execution_lease import LeaseBusy, LeaseLost, LeaseToken
from app.domain.interview.scheduling.ledger import (
    InvocationCommitReceipt,
    InvocationIdentity,
    InvocationLedgerEntry,
    LogicalEffectConflict,
    accept_commit,
)


class InMemoryAgentInvocationLedger:
    """Fenced ledger with the same logical lifecycle as the durable adapter.

    This adapter is intentionally limited to tests and local development.  It
    provides deterministic clock injection so every crash/reclaim window can
    be exercised without a database or wall-clock sleeps.
    """

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._entries: dict[tuple[str, str, int], InvocationLedgerEntry] = {}
        self._deleted_execution_ids: set[str] = set()
        self._lock = RLock()

    def get(self, identity: InvocationIdentity) -> InvocationLedgerEntry | None:
        with self._lock:
            return self._entries.get(identity.key)

    def prepare(self, entry: InvocationLedgerEntry) -> InvocationLedgerEntry:
        with self._lock:
            if entry.execution_id in self._deleted_execution_ids:
                raise RuntimeError("deleted execution cannot prepare invocations")
            existing = self._entries.get(entry.identity_key)
            if existing is None:
                self._entries[entry.identity_key] = entry
                return entry
            if (
                existing.agent_id != entry.agent_id
                or existing.skill != entry.skill
                or existing.request_digest != entry.request_digest
            ):
                raise LogicalEffectConflict(
                    "logical invocation was prepared with different inputs"
                )
            return existing

    def acquire(
        self,
        identity: InvocationIdentity,
        *,
        owner_id: str,
        lease_seconds: int,
        now: datetime | None = None,
    ) -> LeaseToken:
        if isinstance(lease_seconds, bool) or lease_seconds < 1:
            raise ValueError("lease_seconds must be positive")
        resolved_now = now or self._clock()
        with self._lock:
            current = self._require(identity)
            if current.status == "COMPLETED":
                raise LeaseLost("completed invocation cannot be acquired")
            if (
                current.lease_expires_at is not None
                and current.lease_expires_at > resolved_now
            ):
                if current.lease_owner != owner_id:
                    raise LeaseBusy("invocation lease is owned by another worker")
                return self._lease(identity, current)
            fence = current.fencing_version + 1
            token = uuid4().hex
            expires_at = resolved_now + timedelta(seconds=lease_seconds)
            updated = current.model_copy(
                update={
                    "status": "RUNNING",
                    "lease_owner": owner_id,
                    "lease_token": token,
                    "lease_expires_at": expires_at,
                    "fencing_version": fence,
                    "started_at": current.started_at or resolved_now,
                    "updated_at": resolved_now,
                    "finished_at": None,
                    "error_result": None,
                }
            )
            self._entries[identity.key] = updated
            return self._lease(identity, updated)

    def mark_running(
        self,
        identity: InvocationIdentity,
        *,
        lease_owner: str,
        fencing_version: int,
    ) -> InvocationLedgerEntry:
        with self._lock:
            current = self._require(identity)
            self._assert_owner(current, lease_owner, fencing_version, self._clock())
            return current

    def mark_completed(
        self,
        identity: InvocationIdentity,
        *,
        artifact_ref: str,
        lease_owner: str,
        fencing_version: int,
    ) -> InvocationLedgerEntry:
        self.commit(
            identity,
            artifact_ref=artifact_ref,
            lease_owner=lease_owner,
            fencing_version=fencing_version,
        )
        return self._require(identity)

    def commit(
        self,
        identity: InvocationIdentity,
        *,
        artifact_ref: str,
        lease_owner: str,
        fencing_version: int,
    ) -> InvocationCommitReceipt:
        with self._lock:
            current = self._require(identity)
            candidate = InvocationCommitReceipt(
                identity=identity,
                artifact_ref=artifact_ref,
                fencing_version=fencing_version,
                committed_at=self._clock(),
            )
            if current.status == "COMPLETED":
                existing = InvocationCommitReceipt(
                    identity=identity,
                    artifact_ref=current.artifact_ref or "",
                    fencing_version=current.fencing_version,
                    committed_at=current.finished_at or current.updated_at,
                )
                return accept_commit(existing, candidate)
            self._assert_owner(
                current,
                lease_owner,
                fencing_version,
                candidate.committed_at,
            )
            updated = current.model_copy(
                update={
                    "status": "COMPLETED",
                    "artifact_ref": artifact_ref,
                    "updated_at": candidate.committed_at,
                    "finished_at": candidate.committed_at,
                }
            )
            self._entries[identity.key] = updated
            return candidate

    def mark_failed(
        self,
        identity: InvocationIdentity,
        *,
        error_result: dict[str, object],
        lease_owner: str,
        fencing_version: int,
    ) -> InvocationLedgerEntry:
        with self._lock:
            current = self._require(identity)
            now = self._clock()
            self._assert_owner(current, lease_owner, fencing_version, now)
            updated = current.model_copy(
                update={
                    "status": "FAILED",
                    "error_result": error_result,
                    "lease_owner": None,
                    "lease_token": None,
                    "lease_expires_at": None,
                    "updated_at": now,
                    "finished_at": now,
                }
            )
            self._entries[identity.key] = updated
            return updated

    def renew(
        self,
        identity: InvocationIdentity,
        lease: LeaseToken,
        *,
        lease_seconds: int,
        now: datetime | None = None,
    ) -> LeaseToken:
        resolved_now = now or self._clock()
        with self._lock:
            current = self._require(identity)
            lease.assert_current(self._lease(identity, current), now=resolved_now)
            renewed = current.model_copy(
                update={
                    "lease_expires_at": resolved_now
                    + timedelta(seconds=lease_seconds),
                    "updated_at": resolved_now,
                }
            )
            self._entries[identity.key] = renewed
            return self._lease(identity, renewed)

    def expire(
        self,
        identity: InvocationIdentity,
        *,
        now: datetime | None = None,
    ) -> bool:
        resolved_now = now or self._clock()
        with self._lock:
            current = self._require(identity)
            if (
                current.lease_expires_at is None
                or current.lease_expires_at > resolved_now
            ):
                return False
            self._entries[identity.key] = current.model_copy(
                update={
                    "lease_owner": None,
                    "lease_token": None,
                    "lease_expires_at": None,
                    "updated_at": resolved_now,
                }
            )
            return True

    def reclaim(
        self,
        identity: InvocationIdentity,
        *,
        owner_id: str,
        lease_seconds: int,
        now: datetime | None = None,
    ) -> LeaseToken:
        resolved_now = now or self._clock()
        current = self._require(identity)
        if (
            current.lease_expires_at is not None
            and current.lease_expires_at > resolved_now
        ):
            raise LeaseBusy("cannot reclaim a live invocation lease")
        return self.acquire(
            identity,
            owner_id=owner_id,
            lease_seconds=lease_seconds,
            now=resolved_now,
        )

    def assert_lease(
        self,
        identity: InvocationIdentity,
        lease: LeaseToken,
        *,
        now: datetime | None = None,
    ) -> None:
        with self._lock:
            current = self._require(identity)
            lease.assert_current(
                self._lease(identity, current),
                now=now or self._clock(),
            )

    def delete_execution(self, execution_id: str) -> int:
        with self._lock:
            keys = [
                key for key in self._entries if key[0] == execution_id
            ]
            for key in keys:
                self._entries.pop(key, None)
            self._deleted_execution_ids.add(execution_id)
            return len(keys)

    def _require(self, identity: InvocationIdentity) -> InvocationLedgerEntry:
        entry = self._entries.get(identity.key)
        if entry is None:
            raise KeyError(f"unknown invocation identity: {identity.key}")
        return entry

    @staticmethod
    def _resource_id(identity: InvocationIdentity) -> str:
        return ":".join(map(str, identity.key))

    @classmethod
    def _lease(
        cls,
        identity: InvocationIdentity,
        entry: InvocationLedgerEntry,
    ) -> LeaseToken:
        if not (
            entry.lease_owner
            and entry.lease_token
            and entry.lease_expires_at
            and entry.fencing_version > 0
        ):
            raise LeaseLost("invocation has no active lease")
        return LeaseToken(
            resource_id=cls._resource_id(identity),
            owner_id=entry.lease_owner,
            token=entry.lease_token,
            fencing_version=entry.fencing_version,
            expires_at=entry.lease_expires_at,
        )

    @staticmethod
    def _assert_owner(
        current: InvocationLedgerEntry,
        owner: str,
        fence: int,
        now: datetime,
    ) -> None:
        if current.status != "RUNNING":
            raise LeaseLost("invocation is not running")
        if current.lease_owner != owner or current.fencing_version != fence:
            raise LeaseLost("stale worker cannot mutate invocation")
        if current.lease_expires_at is None or current.lease_expires_at <= now:
            raise LeaseLost("invocation lease expired")


__all__ = ["InMemoryAgentInvocationLedger"]
