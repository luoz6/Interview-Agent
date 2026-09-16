from __future__ import annotations

from app.domain.report.artifact import (
    PublishReportArtifact,
    ReportArtifact,
    ReportHead,
    ReportJobV2,
)


class ReportArtifactConflict(RuntimeError):
    pass


class ReportArtifactNotFound(ReportArtifactConflict):
    pass


class ReportArtifactStore:
    def enqueue_job(
        self,
        *,
        session_id: str,
        job_kind: str = "initial",
        source_report_id: str | None = None,
        parent_job_id: str | None = None,
        activate_on_success: bool = True,
        idempotency_key: str | None = None,
    ) -> ReportJobV2: ...

    def claim_job(self, job_id: str, *, worker_id: str) -> ReportJobV2: ...

    def requeue_failed(self, job_id: str) -> ReportJobV2: ...

    def fail_job(self, job_id: str, *, error_code: str) -> ReportJobV2: ...

    def publish(
        self,
        job_id: str,
        payload: PublishReportArtifact,
        *,
        worker_id: str,
    ) -> ReportArtifact: ...

    def get_artifact(self, report_id: str) -> ReportArtifact: ...

    def list_artifacts(self, session_id: str) -> list[ReportArtifact]: ...

    def get_head(self, session_id: str) -> ReportHead: ...

    def get_latest_job(self, session_id: str) -> ReportJobV2 | None: ...

    def list_jobs(self, session_id: str) -> list[ReportJobV2]: ...

    def get_job_by_idempotency_key(
        self, session_id: str, idempotency_key: str
    ) -> ReportJobV2 | None: ...

    def delete_session_history(self, session_id: str) -> int: ...


def report_job_request_matches(
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


__all__ = [
    "ReportArtifactConflict",
    "ReportArtifactNotFound",
    "ReportArtifactStore",
    "report_job_request_matches",
]
