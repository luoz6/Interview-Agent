"""Stable workflow ownership contracts and deterministic identities."""

from __future__ import annotations

import hashlib


class WorkflowThreadBusy(RuntimeError):
    """The workflow thread is currently owned by another executor."""


class WorkflowThreadLockLost(RuntimeError):
    """The dedicated PostgreSQL session that owned the lock was lost."""


class GenerationLeaseLost(RuntimeError):
    """The active generation attempt lease is no longer owned."""


class ReportLeaseLost(RuntimeError):
    """The active Report Job lease is no longer owned."""


class FencedWriteRejected(RuntimeError):
    """A stale owner attempted a write guarded by a fencing predicate."""


class ReviewEffectLeaseLost(FencedWriteRejected):
    """The active Review Effect claim is no longer provably owned."""


class ReviewEffectBusy(RuntimeError):
    """A review provider operation is owned by another live claim."""


class ReviewEffectConflict(RuntimeError):
    """An effect operation key was reused with conflicting immutable data."""


class ProjectionConflict(RuntimeError):
    """LangGraph state diverged from its authoritative business projection."""


class ReportCommitConflict(RuntimeError):
    """The fenced final Review projections could not commit atomically."""


def validate_workflow_identifier(value: str, *, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def interview_thread_identity(session_id: str) -> str:
    return f"interview:{validate_workflow_identifier(session_id, name='session_id')}"


def review_thread_identity(job_id: str) -> str:
    return f"review:{validate_workflow_identifier(job_id, name='job_id')}"


def advisory_lock_key(identity: str) -> int:
    canonical = validate_workflow_identifier(identity, name="identity")
    digest = hashlib.sha256(canonical.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


__all__ = [
    "FencedWriteRejected",
    "GenerationLeaseLost",
    "ProjectionConflict",
    "ReportCommitConflict",
    "ReportLeaseLost",
    "ReviewEffectBusy",
    "ReviewEffectConflict",
    "ReviewEffectLeaseLost",
    "WorkflowThreadBusy",
    "WorkflowThreadLockLost",
    "advisory_lock_key",
    "interview_thread_identity",
    "review_thread_identity",
    "validate_workflow_identifier",
]
