"""Reusable lease and fencing capability for durable execution owners."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


class LeaseLost(RuntimeError):
    """The active lease is no longer owned or has expired."""

    code = "lease_lost"
    retryable = True


class LeaseBusy(RuntimeError):
    """A live lease is owned by another worker."""

    code = "lease_busy"
    retryable = True


@dataclass(frozen=True)
class LeaseToken:
    resource_id: str
    owner_id: str
    token: str = field(repr=False)
    fencing_version: int
    expires_at: datetime

    def __post_init__(self) -> None:
        _require_non_empty("resource_id", self.resource_id)
        _require_non_empty("owner_id", self.owner_id)
        _require_non_empty("token", self.token)
        if isinstance(self.fencing_version, bool) or self.fencing_version < 1:
            raise ValueError("fencing_version must be a positive integer")
        _require_aware_datetime("expires_at", self.expires_at)

    def is_expired(self, *, now: datetime | None = None) -> bool:
        resolved_now = now or datetime.now(timezone.utc)
        _require_aware_datetime("now", resolved_now)
        return self.expires_at <= resolved_now

    def assert_current(
        self,
        current: "LeaseToken",
        *,
        now: datetime | None = None,
    ) -> None:
        if self.resource_id != current.resource_id:
            raise LeaseLost("lease resource identity changed")
        if (
            self.owner_id != current.owner_id
            or self.token != current.token
            or self.fencing_version != current.fencing_version
        ):
            raise LeaseLost("lease ownership or fencing identity changed")
        if current.is_expired(now=now):
            raise LeaseLost("lease expired")


@dataclass(frozen=True)
class FencedMutation:
    resource_id: str
    operation: str
    lease: LeaseToken
    idempotency_key: str = field(repr=False)

    def __post_init__(self) -> None:
        _require_non_empty("resource_id", self.resource_id)
        _require_non_empty("operation", self.operation)
        _require_non_empty("idempotency_key", self.idempotency_key)
        if self.resource_id != self.lease.resource_id:
            raise ValueError("mutation resource_id must match lease resource_id")

    def authorize(
        self,
        current_lease: LeaseToken,
        *,
        now: datetime | None = None,
    ) -> None:
        self.lease.assert_current(current_lease, now=now)


def _require_non_empty(name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")


def _require_aware_datetime(name: str, value: datetime) -> None:
    if not isinstance(value, datetime):
        raise TypeError(f"{name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


__all__ = ["FencedMutation", "LeaseBusy", "LeaseLost", "LeaseToken"]
