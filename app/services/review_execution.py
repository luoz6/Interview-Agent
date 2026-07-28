from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Iterator

from app.services.workflow_thread_lock import ReportLeaseLost


@dataclass(frozen=True)
class ReviewExecutionLease:
    job_id: str
    worker_id: str
    lease_token: str


_CURRENT_REVIEW_LEASE: ContextVar[ReviewExecutionLease | None] = ContextVar(
    "current_review_execution_lease", default=None
)


@contextmanager
def bind_review_execution_lease(
    *, job_id: str, worker_id: str, lease_token: str
) -> Iterator[ReviewExecutionLease]:
    lease = ReviewExecutionLease(
        job_id=job_id,
        worker_id=worker_id,
        lease_token=lease_token,
    )
    token = _CURRENT_REVIEW_LEASE.set(lease)
    try:
        yield lease
    finally:
        _CURRENT_REVIEW_LEASE.reset(token)


def current_review_execution_lease(job_id: str) -> ReviewExecutionLease:
    lease = _CURRENT_REVIEW_LEASE.get()
    if lease is None or lease.job_id != job_id:
        raise ReportLeaseLost("review execution has no active Report Job lease")
    return lease
