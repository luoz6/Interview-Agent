from __future__ import annotations

from datetime import datetime, timedelta, timezone
from threading import RLock
from uuid import uuid4

from app.domain.interview.session_deletion import SessionDeletionJob


class InMemorySessionDeletionJobStore:
    def __init__(self, *, clock=None, job_id_factory=None) -> None:
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.job_id_factory = job_id_factory or (
            lambda: f"delete-{uuid4().hex}"
        )
        self._lock = RLock()
        self._jobs: dict[str, SessionDeletionJob] = {}
        self._session_jobs: dict[str, str] = {}

    def request(self, session_id):
        with self._lock:
            existing_id = self._session_jobs.get(session_id)
            if existing_id is not None:
                return self._jobs[existing_id]
            now = self.clock()
            job = SessionDeletionJob(
                job_id=self.job_id_factory(),
                session_id=session_id,
                status="queued",
                created_at=now,
                updated_at=now,
            )
            self._jobs[job.job_id] = job
            self._session_jobs[session_id] = job.job_id
            return job

    def get_for_session(self, session_id):
        with self._lock:
            job_id = self._session_jobs.get(session_id)
            return self._jobs.get(job_id) if job_id else None

    def claim(self, *, worker_id, lease_seconds):
        if lease_seconds <= 0:
            raise ValueError("deletion lease must be positive")
        with self._lock:
            now = self.clock()
            job = next(
                (
                    item
                    for item in self._jobs.values()
                    if item.status in {"queued", "failed"}
                    or (
                        item.status == "running"
                        and item.lease_expires_at is not None
                        and item.lease_expires_at <= now
                    )
                ),
                None,
            )
            if job is None:
                return None
            claimed = job.model_copy(
                update={
                    "status": "running",
                    "attempt_count": job.attempt_count + 1,
                    "lease_owner": worker_id,
                    "lease_token": uuid4().hex,
                    "lease_expires_at": now + timedelta(seconds=lease_seconds),
                    "fencing_version": job.fencing_version + 1,
                    "updated_at": now,
                    "error_code": None,
                }
            )
            self._jobs[job.job_id] = claimed
            return claimed

    def complete(self, job, *, safe_counts):
        with self._lock:
            current = self._jobs[job.job_id]
            self._require_owned_claim(current, job)
            now = self.clock()
            completed = current.model_copy(
                update={
                    "status": "completed",
                    "safe_counts": dict(safe_counts),
                    "lease_owner": None,
                    "lease_token": None,
                    "lease_expires_at": None,
                    "updated_at": now,
                    "completed_at": now,
                }
            )
            self._jobs[job.job_id] = completed
            return completed

    def fail(self, job, *, error_code):
        with self._lock:
            current = self._jobs[job.job_id]
            self._require_owned_claim(current, job)
            failed = current.model_copy(
                update={
                    "status": "failed",
                    "error_code": error_code,
                    "lease_owner": None,
                    "lease_token": None,
                    "lease_expires_at": None,
                    "updated_at": self.clock(),
                }
            )
            self._jobs[job.job_id] = failed
            return failed

    @staticmethod
    def _require_owned_claim(current, claim) -> None:
        if (
            current.status != "running"
            or current.lease_owner != claim.lease_owner
            or current.lease_token != claim.lease_token
            or current.fencing_version != claim.fencing_version
        ):
            raise RuntimeError("session deletion lease was lost")


__all__ = ["InMemorySessionDeletionJobStore"]
