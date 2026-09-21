"""Canonical durable invocation ledger Port."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from app.domain.execution_lease import LeaseToken
from app.domain.interview.scheduling.ledger import (
    InvocationCommitReceipt,
    InvocationIdentity,
    InvocationLedgerEntry,
)


@runtime_checkable
class AgentInvocationLedgerPort(Protocol):
    """Persist and retrieve the facts for one logical Agent invocation."""

    def get(
        self,
        identity: InvocationIdentity,
    ) -> InvocationLedgerEntry | None:
        """Return the durable entry for ``identity`` if it exists."""

    def prepare(self, entry: InvocationLedgerEntry) -> InvocationLedgerEntry:
        """Create or idempotently return a PREPARED invocation entry."""

    def mark_running(
        self,
        identity: InvocationIdentity,
        *,
        lease_owner: str,
        fencing_version: int,
    ) -> InvocationLedgerEntry:
        """Record that a leased worker began execution."""

    def mark_completed(
        self,
        identity: InvocationIdentity,
        *,
        artifact_ref: str,
        lease_owner: str,
        fencing_version: int,
    ) -> InvocationLedgerEntry:
        """Commit one output artifact for the logical invocation."""

    def commit(
        self,
        identity: InvocationIdentity,
        *,
        artifact_ref: str,
        lease_owner: str,
        fencing_version: int,
    ) -> InvocationCommitReceipt:
        """Persist the artifact receipt and complete the ledger atomically.

        Repeating the same logical commit returns an ``ALREADY_COMMITTED``
        receipt for the original artifact.  A stale fencing version is rejected
        and cannot replace a committed artifact.
        """

    def mark_failed(
        self,
        identity: InvocationIdentity,
        *,
        error_result: dict[str, object],
        lease_owner: str,
        fencing_version: int,
    ) -> InvocationLedgerEntry:
        """Record a terminal Agent failure."""

    def acquire(
        self,
        identity: InvocationIdentity,
        *,
        owner_id: str,
        lease_seconds: int,
        now: datetime | None = None,
    ) -> LeaseToken:
        """Acquire or reclaim a lease and advance its fencing version."""

    def renew(
        self,
        identity: InvocationIdentity,
        lease: LeaseToken,
        *,
        lease_seconds: int,
        now: datetime | None = None,
    ) -> LeaseToken:
        """Renew only the currently fenced lease owner."""

    def expire(
        self,
        identity: InvocationIdentity,
        *,
        now: datetime | None = None,
    ) -> bool:
        """Mark an elapsed lease as expired; return whether it was expired."""

    def reclaim(
        self,
        identity: InvocationIdentity,
        *,
        owner_id: str,
        lease_seconds: int,
        now: datetime | None = None,
    ) -> LeaseToken:
        """Reclaim an expired lease with a strictly newer fence."""

    def assert_lease(
        self,
        identity: InvocationIdentity,
        lease: LeaseToken,
        *,
        now: datetime | None = None,
    ) -> None:
        """Reject stale, foreign, or expired workers before a fenced write."""

    def delete_execution(self, execution_id: str) -> int:
        """Delete all logical invocation facts owned by one execution."""


__all__ = ["AgentInvocationLedgerPort"]
