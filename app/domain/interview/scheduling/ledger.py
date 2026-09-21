"""Neutral durable invocation ledger contracts."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.execution_lease import LeaseLost


InvocationStatus = Literal["PREPARED", "RUNNING", "COMPLETED", "FAILED"]
InvocationCommitStatus = Literal["COMMITTED", "ALREADY_COMMITTED"]
InvocationRecoveryAction = Literal[
    "REDISPATCH",
    "RECLAIM_IF_EXPIRED",
    "OBSERVE_COMMITTED",
    "RETRY_OR_TERMINAL",
]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class InvocationIdentity(BaseModel):
    """Stable identity for one logical Agent attempt."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    execution_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    logical_attempt: int = Field(ge=1)

    @property
    def key(self) -> tuple[str, str, int]:
        return (self.execution_id, self.task_id, self.logical_attempt)


class InvocationLedgerEntry(BaseModel):
    """Durable facts recorded for one logical invocation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    identity: InvocationIdentity
    status: InvocationStatus = "PREPARED"
    agent_id: str = Field(min_length=1)
    skill: str = Field(min_length=1)
    request_digest: str = Field(min_length=1)
    artifact_ref: str | None = Field(default=None, min_length=1)
    error_result: dict[str, Any] | None = None
    lease_owner: str | None = Field(default=None, min_length=1)
    lease_token: str | None = Field(default=None, min_length=1)
    lease_expires_at: datetime | None = None
    fencing_version: int = Field(default=0, ge=0)
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)
    started_at: datetime | None = None
    finished_at: datetime | None = None

    @property
    def execution_id(self) -> str:
        return self.identity.execution_id

    @property
    def task_id(self) -> str:
        return self.identity.task_id

    @property
    def logical_attempt(self) -> int:
        return self.identity.logical_attempt

    @property
    def identity_key(self) -> tuple[str, str, int]:
        return self.identity.key

    @model_validator(mode="after")
    def validate_status_facts(self) -> "InvocationLedgerEntry":
        if self.status == "COMPLETED" and not self.artifact_ref:
            raise ValueError("completed invocation requires artifact_ref")
        if self.status == "FAILED" and not self.error_result:
            raise ValueError("failed invocation requires error_result")
        if self.status == "RUNNING" and not self.lease_owner:
            raise ValueError("running invocation requires lease_owner")
        if self.lease_token is not None and not self.lease_owner:
            raise ValueError("lease_token requires lease_owner")
        if self.lease_expires_at is not None and not self.lease_owner:
            raise ValueError("lease_expires_at requires lease_owner")
        if self.started_at is not None and self.started_at < self.created_at:
            raise ValueError("started_at cannot precede created_at")
        if self.finished_at is not None and self.finished_at < self.created_at:
            raise ValueError("finished_at cannot precede created_at")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot precede created_at")
        return self


class InvocationCommitReceipt(BaseModel):
    """Durable receipt returned after one logical artifact commit."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    identity: InvocationIdentity
    artifact_ref: str = Field(min_length=1)
    fencing_version: int = Field(ge=1)
    status: InvocationCommitStatus = "COMMITTED"
    committed_at: datetime = Field(default_factory=_utc_now)

    @property
    def identity_key(self) -> tuple[str, str, int]:
        return self.identity.key


class LogicalEffectConflict(RuntimeError):
    """A logical attempt tried to commit a different artifact."""

    code = "logical_effect_conflict"
    retryable = False


def accept_commit(
    existing: InvocationCommitReceipt | None,
    candidate: InvocationCommitReceipt,
) -> InvocationCommitReceipt:
    """Accept at most one committed artifact for one logical identity.

    This is a local logical-effect guarantee.  It does not claim that an
    external provider was invoked exactly once before a crash.
    """

    if existing is None:
        return candidate
    if existing.identity_key != candidate.identity_key:
        raise LogicalEffectConflict(
            "commit identity does not match the existing logical attempt"
        )
    if candidate.fencing_version < existing.fencing_version:
        raise LeaseLost("stale worker cannot replace a committed artifact")
    if candidate.artifact_ref != existing.artifact_ref:
        raise LogicalEffectConflict(
            "logical attempt already committed a different artifact"
        )
    return existing.model_copy(update={"status": "ALREADY_COMMITTED"})


def recovery_action(entry: InvocationLedgerEntry) -> InvocationRecoveryAction:
    """Classify restart behavior for each ledger state.

    A scheduler crash before observation re-observes ``COMPLETED``.  A worker
    crash while running (including after obtaining an artifact but before the
    receipt commit) is reclaimed only after the lease expires.  ``PREPARED``
    may be dispatched again; ``FAILED`` is handed to retry/terminal policy.
    """

    return {
        "PREPARED": "REDISPATCH",
        "RUNNING": "RECLAIM_IF_EXPIRED",
        "COMPLETED": "OBSERVE_COMMITTED",
        "FAILED": "RETRY_OR_TERMINAL",
    }[entry.status]


__all__ = [
    "InvocationCommitReceipt",
    "InvocationCommitStatus",
    "InvocationIdentity",
    "InvocationLedgerEntry",
    "LogicalEffectConflict",
    "InvocationRecoveryAction",
    "InvocationStatus",
    "accept_commit",
    "recovery_action",
]
