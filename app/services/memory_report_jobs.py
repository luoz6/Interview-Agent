from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from threading import RLock, Thread, current_thread
from typing import Callable
from uuid import uuid4


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class InMemoryReportJobStore:
    """Application-owned preview queue with job identity and terminal state."""

    def __init__(
        self,
        *,
        runner: Callable[[dict], None] | None = None,
        on_enqueue: Callable[[str], None] | None = None,
        lease_seconds: int = 300,
    ) -> None:
        self.lease_seconds = lease_seconds
        self._runner = runner
        self._on_enqueue = on_enqueue
        self._jobs: dict[str, dict] = {}
        self._session_jobs: dict[str, str] = {}
        self._threads: set[Thread] = set()
        self._lock = RLock()
        self._closed = False

    def enqueue_report_request(self, session_id: str) -> dict:
        with self._lock:
            existing_id = self._session_jobs.get(session_id)
            if existing_id is not None:
                return deepcopy(self._jobs[existing_id])
            now = _utc_now()
            job_id = str(uuid4())
            job = {
                "job_id": job_id,
                "session_id": session_id,
                "status": "queued",
                "lease_owner": None,
                "lease_token": None,
                "lease_expires_at": None,
                "heartbeat_at": None,
                "attempt_count": 0,
                "max_attempts": 3,
                "last_error": None,
                "last_error_code": None,
                "replay_count": 0,
                "review_engine": "legacy",
                "review_graph_schema_version": None,
                "queued_at": now,
                "started_at": None,
                "finished_at": None,
                "updated_at": now,
                "available_at": now,
                "scheduled_attempt": None,
            }
            self._jobs[job_id] = job
            self._session_jobs[session_id] = job_id
            result = deepcopy(job)
        try:
            if self._on_enqueue is not None:
                self._on_enqueue(session_id)
        except Exception:
            with self._lock:
                if self._session_jobs.get(session_id) == job_id:
                    self._session_jobs.pop(session_id, None)
                    self._jobs.pop(job_id, None)
            raise
        self._start_runner(job_id)
        return result

    def get_job_by_session(self, session_id: str) -> dict | None:
        with self._lock:
            job_id = self._session_jobs.get(session_id)
            return deepcopy(self._jobs[job_id]) if job_id is not None else None

    def get_job(self, job_id: str) -> dict | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return deepcopy(job) if job is not None else None

    def claim_next(
        self,
        worker_id: str,
        lease_seconds: int | None = None,
    ) -> dict | None:
        duration = self.lease_seconds if lease_seconds is None else lease_seconds
        with self._lock:
            now = _utc_now()
            candidates = [
                job
                for job in self._jobs.values()
                if job["status"] in {"queued", "retrying"}
                or (
                    job["status"] == "running"
                    and job["lease_expires_at"] is not None
                    and job["lease_expires_at"] <= now
                )
            ]
            if not candidates:
                return None
            job = sorted(candidates, key=lambda item: item["queued_at"])[0]
            job["status"] = "running"
            job["lease_owner"] = worker_id
            job["lease_token"] = str(uuid4())
            job["lease_expires_at"] = now + timedelta(seconds=duration)
            job["heartbeat_at"] = now
            job["attempt_count"] += 1
            job["started_at"] = job["started_at"] or now
            job["updated_at"] = now
            return deepcopy(job)

    def heartbeat(
        self,
        job_id: str,
        *,
        worker_id: str,
        lease_token: str,
        lease_seconds: int | None = None,
    ) -> bool:
        duration = self.lease_seconds if lease_seconds is None else lease_seconds
        with self._lock:
            job = self._jobs.get(job_id)
            now = _utc_now()
            if (
                job is None
                or job["status"] != "running"
                or job["lease_owner"] != worker_id
                or job["lease_token"] != lease_token
                or job["lease_expires_at"] <= now
            ):
                return False
            job["heartbeat_at"] = now
            job["lease_expires_at"] = now + timedelta(seconds=duration)
            job["updated_at"] = now
            return True

    def mark_completed(self, job_id: str) -> dict | None:
        return self._mark_terminal(job_id, status="completed")

    def mark_failed(
        self,
        job_id: str,
        error: str,
        *,
        error_code: str | None = None,
    ) -> dict | None:
        return self._mark_terminal(
            job_id,
            status="failed",
            error=error,
            error_code=error_code,
        )

    def mark_retryable_failure(
        self,
        job_id: str,
        error: str,
        *,
        error_code: str | None = None,
    ) -> dict | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job["status"] in {"completed", "failed"}:
                return deepcopy(job) if job is not None else None
            if job["attempt_count"] >= job["max_attempts"]:
                return self._mark_terminal(
                    job_id,
                    status="failed",
                    error=error,
                    error_code=error_code or "report_retry_exhausted",
                )
            now = _utc_now()
            job.update(
                status="retrying",
                lease_owner=None,
                lease_token=None,
                lease_expires_at=None,
                last_error=error,
                last_error_code=error_code,
                updated_at=now,
            )
            result = deepcopy(job)
        self._start_runner(job_id)
        return result

    def requeue_failed(self, session_id: str) -> dict:
        with self._lock:
            job_id = self._session_jobs.get(session_id)
            if job_id is None or self._jobs[job_id]["status"] != "failed":
                raise ValueError("report job is not failed")
            job = self._jobs[job_id]
            now = _utc_now()
            job.update(
                status="queued",
                lease_owner=None,
                lease_token=None,
                lease_expires_at=None,
                heartbeat_at=None,
                attempt_count=0,
                last_error=None,
                last_error_code=None,
                replay_count=job["replay_count"] + 1,
                started_at=None,
                finished_at=None,
                updated_at=now,
                available_at=now,
            )
            result = deepcopy(job)
        self._start_runner(job_id)
        return result

    def repair_orphan_processing_reports(self) -> int:
        return 0

    def shutdown(self, *, wait: bool = True) -> None:
        with self._lock:
            self._closed = True
            threads = list(self._threads)
        if wait:
            for thread in threads:
                thread.join(timeout=5)

    def _start_runner(self, job_id: str) -> None:
        if self._runner is None:
            return
        with self._lock:
            if self._closed:
                return
            thread = Thread(
                target=self._run_job,
                args=(job_id,),
                daemon=True,
                name=f"preview-report-{job_id[:8]}",
            )
            self._threads.add(thread)
            thread.start()

    def _run_job(self, job_id: str) -> None:
        current = current_thread()
        try:
            job = self._claim_job(job_id, worker_id="preview-report-worker")
            if job is None:
                return
            assert self._runner is not None
            self._runner(job)
            self.mark_completed(job_id)
        except Exception as exc:
            self.mark_failed(
                job_id,
                "preview report generation failed",
                error_code=type(exc).__name__,
            )
        finally:
            # Finished daemon threads are harmless; prune all inactive entries.
            with self._lock:
                self._threads = {thread for thread in self._threads if thread.is_alive()}
                self._threads.discard(current)

    def _claim_job(self, job_id: str, *, worker_id: str) -> dict | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job["status"] not in {"queued", "retrying"}:
                return None
            now = _utc_now()
            job["status"] = "running"
            job["lease_owner"] = worker_id
            job["lease_token"] = str(uuid4())
            job["lease_expires_at"] = now + timedelta(seconds=self.lease_seconds)
            job["heartbeat_at"] = now
            job["attempt_count"] += 1
            job["started_at"] = job["started_at"] or now
            job["updated_at"] = now
            return deepcopy(job)

    def _mark_terminal(
        self,
        job_id: str,
        *,
        status: str,
        error: str | None = None,
        error_code: str | None = None,
    ) -> dict | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            if job["status"] in {"completed", "failed"}:
                return deepcopy(job)
            now = _utc_now()
            job.update(
                status=status,
                lease_owner=None,
                lease_token=None,
                lease_expires_at=None,
                last_error=error,
                last_error_code=error_code,
                finished_at=now,
                updated_at=now,
            )
            return deepcopy(job)
