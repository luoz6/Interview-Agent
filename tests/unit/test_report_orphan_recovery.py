"""Unit tests for report progress and orphan-state projection."""

from datetime import datetime, timedelta, timezone

from app.api.reports.routes import _report_progress_detail
from app.services.report import ReportProgress, ReportRecord


def test_stale_processing_record_without_job_is_projected_as_orphaned():
    record = ReportRecord(
        status="processing",
        progress=ReportProgress(
            stage="retrieving",
            percent=20,
            message="Retrieving role-specific knowledge references.",
        ),
        created_at=(datetime.now(timezone.utc) - timedelta(minutes=5))
        .isoformat()
        .replace("+00:00", "Z"),
    )

    detail = _report_progress_detail("s1", record, job=None)

    assert detail["status"] == "orphaned"
    assert detail["stage"] == "orphaned"
    assert detail["stalled"] is True
    assert detail["orphaned"] is True
    assert detail["retryable"] is True
    assert detail["error"]["code"] == "report_job_missing"


def test_active_job_projects_attempt_and_independent_heartbeat():
    now = datetime.now(timezone.utc)
    record = ReportRecord(
        status="processing",
        progress=ReportProgress(
            stage="analyzing",
            percent=60,
            message="Analyzing report.",
        ),
    )
    job = {
        "job_id": "job-1",
        "status": "running",
        "attempt_count": 2,
        "max_attempts": 3,
        "started_at": now - timedelta(seconds=20),
        "updated_at": now,
        "heartbeat_at": now,
        "lease_expires_at": now + timedelta(seconds=45),
    }

    detail = _report_progress_detail("s1", record, job=job)

    assert detail["report_job_id"] == "job-1"
    assert detail["attempt"] == 2
    assert detail["max_attempts"] == 3
    assert detail["heartbeat_at"] is not None
    assert detail["stalled"] is False
    assert detail["orphaned"] is False


def test_queue_failure_is_structured_and_retryable():
    record = ReportRecord(
        status="failed",
        error="report queue unavailable",
    )

    detail = _report_progress_detail("s1", record, job=None)

    assert detail["status"] == "failed"
    assert detail["retryable"] is True
    assert detail["error"] == {
        "code": "report_enqueue_unavailable",
        "message": "report queue unavailable",
        "retryable": True,
    }
