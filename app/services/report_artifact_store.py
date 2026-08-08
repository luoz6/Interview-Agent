from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from threading import RLock
from typing import Callable
from uuid import uuid4

from app.services.report_artifact import (
    PublishReportArtifact,
    ReportArtifact,
    ReportHead,
    ReportJobV2,
    report_artifact_sha256,
)


class ReportArtifactConflict(RuntimeError):
    pass


class ReportArtifactNotFound(ReportArtifactConflict):
    pass


class ReportArtifactStore:
    def enqueue_job(self, *, session_id: str, job_kind: str = "initial", source_report_id: str | None = None, parent_job_id: str | None = None, activate_on_success: bool = True, idempotency_key: str | None = None) -> ReportJobV2: ...
    def claim_job(self, job_id: str, *, worker_id: str) -> ReportJobV2: ...
    def requeue_failed(self, job_id: str) -> ReportJobV2: ...
    def fail_job(self, job_id: str, *, error_code: str) -> ReportJobV2: ...
    def publish(self, job_id: str, payload: PublishReportArtifact, *, worker_id: str) -> ReportArtifact: ...
    def get_artifact(self, report_id: str) -> ReportArtifact: ...
    def list_artifacts(self, session_id: str) -> list[ReportArtifact]: ...
    def get_head(self, session_id: str) -> ReportHead: ...
    def get_latest_job(self, session_id: str) -> ReportJobV2 | None: ...
    def list_jobs(self, session_id: str) -> list[ReportJobV2]: ...
    def get_job_by_idempotency_key(self, session_id: str, idempotency_key: str) -> ReportJobV2 | None: ...
    def delete_session_history(self, session_id: str) -> int: ...


def _job_request_matches(
    job: ReportJobV2,
    *,
    job_kind: str,
    source_report_id: str | None,
    parent_job_id: str | None,
    activate_on_success: bool,
) -> bool:
    return (
        job.job_kind == job_kind
        and job.source_report_id == source_report_id
        and job.parent_job_id == parent_job_id
        and job.activate_on_success is activate_on_success
    )


class InMemoryReportArtifactStore:
    def __init__(self, *, clock: Callable[[], datetime] | None = None) -> None:
        self._lock = RLock()
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._jobs: dict[str, ReportJobV2] = {}
        self._job_keys: dict[tuple[str, str], str] = {}
        self._artifacts: dict[str, ReportArtifact] = {}
        self._artifact_by_source_job: dict[str, str] = {}
        self._heads: dict[str, ReportHead] = {}
        self._deleted_sessions: set[str] = set()
        self._failure_step: str | None = None

    def inject_failure(self, step: str | None) -> None:
        self._failure_step = step

    def enqueue_job(
        self,
        *,
        session_id: str,
        job_kind: str = "initial",
        source_report_id: str | None = None,
        parent_job_id: str | None = None,
        activate_on_success: bool = True,
        idempotency_key: str | None = None,
    ) -> ReportJobV2:
        key = idempotency_key or f"{job_kind}:{uuid4()}"
        now = self._clock()
        with self._lock:
            if session_id in self._deleted_sessions:
                raise ReportArtifactNotFound("session is deleting or deleted")
            existing_id = self._job_keys.get((session_id, key))
            if existing_id is not None:
                existing = self._jobs[existing_id]
                if not _job_request_matches(
                    existing,
                    job_kind=job_kind,
                    source_report_id=source_report_id,
                    parent_job_id=parent_job_id,
                    activate_on_success=activate_on_success,
                ):
                    raise ReportArtifactConflict(
                        "idempotency key payload conflicts"
                    )
                return deepcopy(existing)
            if source_report_id is not None:
                source = self._artifacts.get(source_report_id)
                if source is None or source.session_id != session_id:
                    raise ReportArtifactConflict("source report does not belong to session")
            if any(
                job.session_id == session_id
                and job.status in {"queued", "running", "retrying"}
                for job in self._jobs.values()
            ):
                raise ReportArtifactConflict("session already has an active report job")
            job = ReportJobV2(
                job_id=str(uuid4()),
                session_id=session_id,
                job_kind=job_kind,  # type: ignore[arg-type]
                parent_job_id=parent_job_id,
                source_report_id=source_report_id,
                activate_on_success=activate_on_success,
                status="queued",
                idempotency_key=key,
                created_at=now,
                updated_at=now,
            )
            self._jobs[job.job_id] = job
            self._job_keys[(session_id, key)] = job.job_id
            self._heads.setdefault(
                session_id,
                ReportHead(session_id=session_id, updated_at=now),
            )
            self._heads[session_id] = self._heads[session_id].model_copy(
                update={"latest_job_id": job.job_id, "updated_at": now}
            )
            return deepcopy(job)

    def claim_job(self, job_id: str, *, worker_id: str) -> ReportJobV2:
        with self._lock:
            job = self._get_job(job_id)
            if job.status not in {"queued", "retrying"}:
                raise ReportArtifactConflict("job is not claimable")
            updated = job.model_copy(
                update={
                    "status": "running",
                    "lease_owner": worker_id,
                    "lease_token": str(uuid4()),
                    "fencing_version": job.fencing_version + 1,
                    "updated_at": self._clock(),
                }
            )
            self._jobs[job_id] = updated
            return deepcopy(updated)

    def requeue_failed(self, job_id: str) -> ReportJobV2:
        with self._lock:
            job = self._get_job(job_id)
            if job.status != "failed":
                raise ReportArtifactConflict("only failed jobs may be requeued")
            updated = job.model_copy(
                update={"status": "queued", "error_code": None, "updated_at": self._clock()}
            )
            self._jobs[job_id] = updated
            return deepcopy(updated)

    def fail_job(self, job_id: str, *, error_code: str) -> ReportJobV2:
        with self._lock:
            job = self._get_job(job_id)
            if job.status == "completed":
                raise ReportArtifactConflict("completed job cannot fail")
            updated = job.model_copy(
                update={
                    "status": "failed",
                    "error_code": error_code,
                    "lease_owner": None,
                    "lease_token": None,
                    "updated_at": self._clock(),
                }
            )
            self._jobs[job_id] = updated
            return deepcopy(updated)

    def publish(self, job_id: str, payload: PublishReportArtifact, *, worker_id: str) -> ReportArtifact:
        with self._lock:
            job = self._get_job(job_id)
            if job.status == "completed" and job.report_id:
                existing = self._artifacts[job.report_id]
                if existing.artifact_sha256 != report_artifact_sha256(payload.payload):
                    raise ReportArtifactConflict("replayed job payload conflicts")
                return deepcopy(existing)
            if job.status != "running" or job.lease_owner != worker_id:
                raise ReportArtifactConflict("job fencing token is not active")
            existing_id = self._artifact_by_source_job.get(job_id)
            if existing_id is not None:
                return deepcopy(self._artifacts[existing_id])
            source = self._artifacts.get(job.source_report_id) if job.source_report_id else None
            if source is not None and source.session_id != job.session_id:
                raise ReportArtifactConflict("source artifact belongs to another session")
            current_head = self._heads.get(job.session_id) or ReportHead(
                session_id=job.session_id,
                updated_at=self._clock(),
            )
            supersedes = current_head.active_report_id if job.activate_on_success else None
            if source is not None and supersedes is not None and source.report_id != supersedes:
                raise ReportArtifactConflict("rescore source is not the active report")
            revision = max(
                (item.revision for item in self._artifacts.values() if item.session_id == job.session_id),
                default=0,
            ) + 1
            artifact = ReportArtifact(
                report_id=str(uuid4()),
                session_id=job.session_id,
                revision=revision,
                **payload.model_dump(mode="json"),
                artifact_sha256=report_artifact_sha256(payload.payload),
                source_report_id=job.source_report_id,
                supersedes_report_id=supersedes,
                source_job_id=job_id,
                created_at=self._clock(),
            )
            staged_head = current_head.model_copy(
                update={
                    "active_report_id": artifact.report_id
                    if job.activate_on_success
                    else current_head.active_report_id,
                    "updated_at": self._clock(),
                }
            )
            completed_job = job.model_copy(
                update={
                    "status": "completed",
                    "report_id": artifact.report_id,
                    "lease_owner": None,
                    "lease_token": None,
                    "updated_at": self._clock(),
                }
            )
            staged_artifacts = dict(self._artifacts)
            staged_by_source_job = dict(self._artifact_by_source_job)
            staged_heads = dict(self._heads)
            staged_jobs = dict(self._jobs)
            self._fail_if("before_artifact")
            self._fail_if("artifact")
            staged_artifacts[artifact.report_id] = artifact
            staged_by_source_job[job_id] = artifact.report_id
            self._fail_if("head")
            staged_heads[job.session_id] = staged_head
            self._fail_if("job")
            staged_jobs[job_id] = completed_job
            self._fail_if("review_run")
            self._fail_if("session")
            self._artifacts = staged_artifacts
            self._artifact_by_source_job = staged_by_source_job
            self._heads = staged_heads
            self._jobs = staged_jobs
            return deepcopy(artifact)

    def get_artifact(self, report_id: str) -> ReportArtifact:
        with self._lock:
            try:
                return deepcopy(self._artifacts[report_id])
            except KeyError as exc:
                raise ReportArtifactNotFound("report artifact not found") from exc

    def list_artifacts(self, session_id: str) -> list[ReportArtifact]:
        with self._lock:
            return sorted(
                (deepcopy(item) for item in self._artifacts.values() if item.session_id == session_id),
                key=lambda item: item.revision,
            )

    def get_head(self, session_id: str) -> ReportHead:
        with self._lock:
            head = self._heads.get(session_id)
            if head is None:
                return ReportHead(session_id=session_id, updated_at=self._clock())
            return deepcopy(head)

    def list_jobs(self, session_id: str) -> list[ReportJobV2]:
        with self._lock:
            return sorted(
                (deepcopy(job) for job in self._jobs.values() if job.session_id == session_id),
                key=lambda item: item.created_at,
            )

    def get_job_by_idempotency_key(
        self, session_id: str, idempotency_key: str
    ) -> ReportJobV2 | None:
        with self._lock:
            job_id = self._job_keys.get((session_id, idempotency_key))
            return None if job_id is None else deepcopy(self._jobs[job_id])

    def get_latest_job(self, session_id: str) -> ReportJobV2 | None:
        with self._lock:
            jobs = (job for job in self._jobs.values() if job.session_id == session_id)
            latest = max(
                jobs,
                key=lambda item: (item.created_at, item.job_id),
                default=None,
            )
            return deepcopy(latest)

    def delete_session_history(self, session_id: str) -> int:
        with self._lock:
            job_ids = {
                job_id
                for job_id, job in self._jobs.items()
                if job.session_id == session_id
            }
            report_ids = {
                report_id
                for report_id, artifact in self._artifacts.items()
                if artifact.session_id == session_id
            }
            deleted = len(job_ids) + len(report_ids) + int(
                session_id in self._heads
            )
            for key, job_id in list(self._job_keys.items()):
                if key[0] == session_id or job_id in job_ids:
                    self._job_keys.pop(key, None)
            for job_id in job_ids:
                self._jobs.pop(job_id, None)
                self._artifact_by_source_job.pop(job_id, None)
            for report_id in report_ids:
                self._artifacts.pop(report_id, None)
            self._heads.pop(session_id, None)
            self._deleted_sessions.add(session_id)
            return deleted

    def _get_job(self, job_id: str) -> ReportJobV2:
        try:
            return self._jobs[job_id]
        except KeyError as exc:
            raise ReportArtifactNotFound("report job not found") from exc

    def _fail_if(self, step: str) -> None:
        if self._failure_step == step:
            self._failure_step = None
            raise RuntimeError(f"injected report artifact failure at {step}")
