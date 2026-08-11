"""Unit tests for report worker orchestration, leases, and error mapping."""

import logging
from threading import Event
from types import SimpleNamespace

from app.services.report import (
    ReportGenerationFailed,
    ReportGenerationTimeout,
    ReportQualityFailed,
)
from app.services.report_worker import run_one_job
from app.services.workflow_thread_lock import ReportLeaseLost
from tests.report_worker_fixtures import make_report


class FakeJobStore:
    def __init__(self, claimed_job: dict | None) -> None:
        self.claimed_job = claimed_job
        self.repair_calls = 0
        self.claim_calls: list[str] = []
        self.completed_calls: list[str] = []
        self.retry_calls: list[tuple[str, str]] = []
        self.failed_calls: list[tuple[str, str]] = []
        self.retry_error_codes: list[str] = []
        self.failed_error_codes: list[str] = []
        self.released_calls: list[tuple[str, str, str]] = []

    def repair_orphan_processing_reports(self) -> int:
        self.repair_calls += 1
        return 0

    def claim_next(self, worker_id: str) -> dict | None:
        self.claim_calls.append(worker_id)
        return self.claimed_job

    def mark_completed(
        self,
        job_id: str,
        *,
        worker_id: str | None = None,
        lease_token: str | None = None,
    ) -> dict:
        self.completed_calls.append(job_id)
        return {
            "job_id": job_id,
            "session_id": self.claimed_job["session_id"],
            "status": "completed",
        }

    def get_job(self, job_id: str) -> dict:
        return dict(self.claimed_job)

    def release_claim_for_retry(
        self, job_id: str, *, worker_id: str, lease_token: str
    ) -> bool:
        self.released_calls.append((job_id, worker_id, lease_token))
        return True

    def mark_retryable_failure(
        self,
        job_id: str,
        error: str,
        *,
        error_code: str,
        worker_id: str | None = None,
        lease_token: str | None = None,
    ) -> dict:
        self.retry_calls.append((job_id, error))
        self.retry_error_codes.append(error_code)
        return {
            "job_id": job_id,
            "session_id": self.claimed_job["session_id"],
            "status": "retrying",
            "last_error": error,
        }

    def mark_failed(
        self,
        job_id: str,
        error: str,
        *,
        error_code: str,
        worker_id: str | None = None,
        lease_token: str | None = None,
    ) -> dict:
        self.failed_calls.append((job_id, error))
        self.failed_error_codes.append(error_code)
        return {
            "job_id": job_id,
            "session_id": self.claimed_job["session_id"],
            "status": "failed",
            "last_error": error,
        }


class FakeStore:
    def __init__(self) -> None:
        self.failed_reports: list[tuple[str, str]] = []

    def fail_report(self, session_id: str, error: str) -> None:
        self.failed_reports.append((session_id, error))


class RecordingSignalStore:
    def __init__(self, *, fail=False) -> None:
        self.calls = []
        self.fail = fail

    def increment(self, *, workflow_type, signal_code):
        self.calls.append((workflow_type, signal_code))
        if self.fail:
            raise RuntimeError("signal database unavailable")


def make_executor(store: FakeStore | None = None):
    return SimpleNamespace(
        store=store or FakeStore(),
        llm=object(),
        vector_store=object(),
    )


def test_run_one_job_returns_none_when_no_job_is_available():
    job_store = FakeJobStore(claimed_job=None)

    result = run_one_job(
        job_store=job_store,
        executor=make_executor(),
        worker_id="worker-1",
    )

    assert result is None
    assert job_store.repair_calls == 1
    assert job_store.claim_calls == ["worker-1"]


def test_durable_job_is_owned_by_review_workflow():
    job = {
        "job_id": "job-1",
        "session_id": "s1",
        "review_engine": "langgraph-review-v1",
    }
    job_store = FakeJobStore(claimed_job=job)
    calls = []
    workflow = SimpleNamespace(
        run_claimed_job=lambda claimed, **kwargs: calls.append(
            (claimed, kwargs)
        )
    )

    result = run_one_job(
        job_store=job_store,
        executor=make_executor(),
        worker_id="worker-1",
        review_workflow=workflow,
    )

    assert result == job
    assert calls == [(job, {"worker_id": "worker-1"})]
    assert job_store.completed_calls == []
    assert job_store.retry_calls == []


def test_durable_thread_busy_outcome_records_exactly_one_incident():
    job = {
        "job_id": "job-1",
        "session_id": "s1",
        "review_engine": "langgraph-review-v1",
    }
    job_store = FakeJobStore(claimed_job=job)
    signals = RecordingSignalStore()
    workflow = SimpleNamespace(
        run_claimed_job=lambda claimed, **kwargs: {
            "status": "workflow_thread_busy"
        }
    )

    result = run_one_job(
        job_store=job_store,
        executor=make_executor(),
        worker_id="worker-1",
        review_workflow=workflow,
        signal_store=signals,
    )

    assert result == job
    assert signals.calls == [("review", "workflow_thread_busy")]


def test_durable_workflow_exception_releases_claim_without_legacy_writes():
    job = {
        "job_id": "job-1",
        "session_id": "s1",
        "review_engine": "langgraph-review-v1",
        "lease_token": "token-1",
    }
    job_store = FakeJobStore(claimed_job=job)
    store = FakeStore()

    def fail_workflow(claimed, **kwargs):
        raise RuntimeError("durable workflow failed")

    result = run_one_job(
        job_store=job_store,
        executor=make_executor(store),
        worker_id="worker-1",
        review_workflow=SimpleNamespace(run_claimed_job=fail_workflow),
    )

    assert result == job
    assert job_store.retry_error_codes == []
    assert job_store.released_calls == [
        ("job-1", "worker-1", "token-1")
    ]
    assert store.failed_reports == []


def test_durable_lease_loss_never_mutates_replacement_job_or_report():
    job = {
        "job_id": "job-1",
        "session_id": "s1",
        "review_engine": "langgraph-review-v1",
    }
    job_store = FakeJobStore(claimed_job=job)
    store = FakeStore()

    def lose_lease(claimed, **kwargs):
        raise ReportLeaseLost("lost")

    result = run_one_job(
        job_store=job_store,
        executor=make_executor(store),
        worker_id="worker-1",
        review_workflow=SimpleNamespace(run_claimed_job=lose_lease),
    )

    assert result == job
    assert job_store.retry_calls == []
    assert job_store.failed_calls == []
    assert store.failed_reports == []


def test_durable_lease_loss_records_one_incident_without_mutating_job():
    job = {
        "job_id": "job-1",
        "session_id": "s1",
        "review_engine": "langgraph-review-v1",
    }
    job_store = FakeJobStore(claimed_job=job)
    signals = RecordingSignalStore()
    error = ReportLeaseLost("renewal ownership unavailable")
    error.__cause__ = RuntimeError(
        "postgresql://private lease_token=private provider payload"
    )

    def lose_lease(claimed, **kwargs):
        raise error

    result = run_one_job(
        job_store=job_store,
        executor=make_executor(),
        worker_id="worker-1",
        review_workflow=SimpleNamespace(run_claimed_job=lose_lease),
        signal_store=signals,
    )

    assert result == job
    assert signals.calls == [("review", "report_lease_lost")]
    assert "private" not in repr(signals.calls)


def test_durable_effect_lease_loss_outcome_records_one_fenced_incident():
    job = {
        "job_id": "job-1",
        "session_id": "s1",
        "review_engine": "langgraph-review-v1",
    }
    job_store = FakeJobStore(claimed_job=job)
    signals = RecordingSignalStore()

    result = run_one_job(
        job_store=job_store,
        executor=make_executor(),
        worker_id="worker-1",
        review_workflow=SimpleNamespace(
            run_claimed_job=lambda claimed, **kwargs: {
                "error_code": "fenced_write_rejected"
            }
        ),
        signal_store=signals,
    )

    assert result == job
    assert signals.calls == [("review", "fenced_write_rejected")]


def test_run_one_job_repairs_orphan_before_claiming(monkeypatch):
    monkeypatch.setattr(
        "app.services.report_worker.execute_report_generation",
        lambda **kwargs: make_report(kwargs["session_id"]),
    )

    class RepairingJobStore(FakeJobStore):
        def repair_orphan_processing_reports(self) -> int:
            self.repair_calls += 1
            self.claimed_job = {"job_id": "job-1", "session_id": "s1"}
            return 1

    job_store = RepairingJobStore(claimed_job=None)

    result = run_one_job(
        job_store=job_store,
        executor=make_executor(),
        worker_id="worker-1",
    )

    assert result is not None
    assert result["status"] == "completed"
    assert job_store.repair_calls == 1
    assert job_store.claim_calls == ["worker-1"]
    assert job_store.completed_calls == ["job-1"]


def test_run_one_job_marks_completed_when_execution_succeeds(monkeypatch):
    monkeypatch.setattr(
        "app.services.report_worker.execute_report_generation",
        lambda **kwargs: make_report(kwargs["session_id"]),
    )
    job_store = FakeJobStore(claimed_job={"job_id": "job-1", "session_id": "s1"})
    store = FakeStore()

    result = run_one_job(
        job_store=job_store,
        executor=make_executor(store),
        worker_id="worker-1",
    )

    assert result["status"] == "completed"
    assert job_store.completed_calls == ["job-1"]
    assert store.failed_reports == []


def test_legacy_report_job_publishes_heartbeat_while_execution_is_active(
    monkeypatch,
):
    class HeartbeatJobStore(FakeJobStore):
        lease_seconds = 0.3

        def __init__(self, claimed_job):
            super().__init__(claimed_job)
            self.heartbeat_seen = Event()
            self.heartbeat_calls = 0

        def assert_lease(self, job_id, *, worker_id, lease_token):
            return True

        def heartbeat(
            self,
            job_id,
            *,
            worker_id,
            lease_token,
            lease_seconds,
        ):
            self.heartbeat_calls += 1
            self.heartbeat_seen.set()
            return True

    job_store = HeartbeatJobStore(
        claimed_job={
            "job_id": "job-1",
            "session_id": "s1",
            "lease_token": "token-1",
        }
    )

    def complete_after_heartbeat(**kwargs):
        assert job_store.heartbeat_seen.wait(timeout=1)
        return make_report(kwargs["session_id"])

    monkeypatch.setattr(
        "app.services.report_worker.execute_report_generation",
        complete_after_heartbeat,
    )

    result = run_one_job(
        job_store=job_store,
        executor=make_executor(),
        worker_id="worker-1",
    )

    assert result["status"] == "completed"
    assert job_store.heartbeat_calls >= 1
    assert job_store.completed_calls == ["job-1"]


def test_legacy_report_job_does_not_commit_terminal_state_after_lease_loss(
    monkeypatch,
):
    class LostHeartbeatJobStore(FakeJobStore):
        lease_seconds = 0.3

        def __init__(self, claimed_job):
            super().__init__(claimed_job)
            self.heartbeat_seen = Event()

        def assert_lease(self, job_id, *, worker_id, lease_token):
            return True

        def heartbeat(
            self,
            job_id,
            *,
            worker_id,
            lease_token,
            lease_seconds,
        ):
            self.heartbeat_seen.set()
            return False

    job = {
        "job_id": "job-1",
        "session_id": "s1",
        "lease_token": "token-1",
    }
    job_store = LostHeartbeatJobStore(claimed_job=job)
    store = FakeStore()

    def finish_after_lease_loss(**kwargs):
        assert job_store.heartbeat_seen.wait(timeout=1)
        return make_report(kwargs["session_id"])

    monkeypatch.setattr(
        "app.services.report_worker.execute_report_generation",
        finish_after_lease_loss,
    )

    result = run_one_job(
        job_store=job_store,
        executor=make_executor(store),
        worker_id="worker-1",
    )

    assert result == job
    assert job_store.completed_calls == []
    assert job_store.retry_calls == []
    assert job_store.failed_calls == []
    assert store.failed_reports == []


def test_run_one_job_logs_when_completion_uses_fallback_report(monkeypatch, caplog):
    def complete_with_fallback(**kwargs):
        report = make_report(kwargs["session_id"])
        report.is_fallback = True
        return report

    monkeypatch.setattr(
        "app.services.report_worker.execute_report_generation",
        complete_with_fallback,
    )
    job_store = FakeJobStore(claimed_job={"job_id": "job-1", "session_id": "s1"})

    with caplog.at_level(logging.WARNING):
        result = run_one_job(
            job_store=job_store,
            executor=make_executor(),
            worker_id="worker-1",
        )

    assert result["status"] == "completed"
    assert "fallback report" in caplog.text.lower()


def test_run_one_job_marks_retryable_failure_for_timeout(monkeypatch):
    def raise_timeout(**kwargs):
        raise ReportGenerationTimeout("report generation timed out")

    monkeypatch.setattr("app.services.report_worker.execute_report_generation", raise_timeout)
    job_store = FakeJobStore(claimed_job={"job_id": "job-1", "session_id": "s1"})
    store = FakeStore()

    result = run_one_job(
        job_store=job_store,
        executor=make_executor(store),
        worker_id="worker-1",
    )

    assert result["status"] == "retrying"
    assert job_store.retry_calls == [("job-1", "report generation timed out")]
    assert job_store.retry_error_codes == ["provider_timeout"]
    assert job_store.failed_calls == []
    assert store.failed_reports == [("s1", "report generation timed out")]


def test_run_one_job_marks_retryable_failure_for_pgvector_unavailable(monkeypatch):
    def raise_retrieval_error(**kwargs):
        raise ReportGenerationFailed("pgvector knowledge store is unavailable")

    monkeypatch.setattr(
        "app.services.report_worker.execute_report_generation",
        raise_retrieval_error,
    )
    job_store = FakeJobStore(claimed_job={"job_id": "job-1", "session_id": "s1"})
    store = FakeStore()

    result = run_one_job(
        job_store=job_store,
        executor=make_executor(store),
        worker_id="worker-1",
    )

    assert result["status"] == "retrying"
    assert job_store.retry_calls == [("job-1", "pgvector knowledge store is unavailable")]
    assert job_store.retry_error_codes == ["provider_unavailable"]
    assert job_store.failed_calls == []
    assert store.failed_reports == [("s1", "pgvector knowledge store is unavailable")]


def test_run_one_job_marks_terminal_failure_for_non_retryable_report_error(monkeypatch):
    def raise_terminal_error(**kwargs):
        raise ReportGenerationFailed("interview is not finished")

    monkeypatch.setattr(
        "app.services.report_worker.execute_report_generation",
        raise_terminal_error,
    )
    job_store = FakeJobStore(claimed_job={"job_id": "job-1", "session_id": "s1"})
    store = FakeStore()

    result = run_one_job(
        job_store=job_store,
        executor=make_executor(store),
        worker_id="worker-1",
    )

    assert result["status"] == "failed"
    assert job_store.retry_calls == []
    assert job_store.failed_calls == [("job-1", "interview is not finished")]
    assert job_store.failed_error_codes == ["domain_validation_failed"]
    assert store.failed_reports == [("s1", "interview is not finished")]


def test_run_one_job_marks_terminal_failure_for_runtime_quality_failure(monkeypatch):
    def raise_quality_failure(**kwargs):
        raise ReportQualityFailed(
            "runtime report quality check failed: summary must include Simplified Chinese text"
        )

    monkeypatch.setattr(
        "app.services.report_worker.execute_report_generation",
        raise_quality_failure,
    )
    job_store = FakeJobStore(claimed_job={"job_id": "job-1", "session_id": "s1"})
    store = FakeStore()

    result = run_one_job(
        job_store=job_store,
        executor=make_executor(store),
        worker_id="worker-1",
    )

    assert result["status"] == "failed"
    assert job_store.retry_calls == []
    assert job_store.failed_calls == [
        (
            "job-1",
            "runtime report quality check failed: summary must include Simplified Chinese text",
        )
    ]
    assert job_store.failed_error_codes == ["domain_validation_failed"]
    assert store.failed_reports == [
        (
            "s1",
            "runtime report quality check failed: summary must include Simplified Chinese text",
        )
    ]
