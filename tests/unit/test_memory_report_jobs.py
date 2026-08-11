from __future__ import annotations

from threading import Event

from app.services.memory_report_jobs import InMemoryReportJobStore


def test_memory_report_job_has_identity_and_runs_outside_response_lifecycle():
    completed = Event()

    def runner(job):
        assert job["session_id"] == "s1"
        completed.set()

    store = InMemoryReportJobStore(runner=runner)

    created = store.enqueue_report_request("s1")

    assert created["job_id"]
    assert completed.wait(timeout=2)
    job = store.get_job_by_session("s1")
    assert job is not None
    assert job["status"] == "completed"
    assert job["heartbeat_at"] is not None


def test_memory_report_enqueue_is_idempotent_per_session():
    store = InMemoryReportJobStore()

    first = store.enqueue_report_request("s1")
    second = store.enqueue_report_request("s1")

    assert first["job_id"] == second["job_id"]


def test_memory_report_enqueue_rolls_back_identity_when_projection_fails():
    def fail_projection(session_id: str) -> None:
        raise RuntimeError("projection unavailable")

    store = InMemoryReportJobStore(on_enqueue=fail_projection)

    try:
        store.enqueue_report_request("s1")
    except RuntimeError as exc:
        assert str(exc) == "projection unavailable"
    else:
        raise AssertionError("enqueue must fail when its report projection fails")

    assert store.get_job_by_session("s1") is None


def test_memory_report_job_requeue_reuses_identity_and_increments_replay():
    store = InMemoryReportJobStore()
    created = store.enqueue_report_request("s1")
    claimed = store.claim_next("worker-1")
    assert claimed is not None
    store.mark_failed(created["job_id"], "failed")

    requeued = store.requeue_failed("s1")

    assert requeued["job_id"] == created["job_id"]
    assert requeued["status"] == "queued"
    assert requeued["replay_count"] == 1


def test_memory_report_failed_terminal_cannot_be_rewritten_by_stale_completion():
    store = InMemoryReportJobStore()
    created = store.enqueue_report_request("s1")
    claimed = store.claim_next("worker-1")
    assert claimed is not None

    failed = store.mark_failed(
        created["job_id"],
        "terminal failure",
        error_code="domain_validation_failed",
    )
    stale_completion = store.mark_completed(created["job_id"])

    assert failed is not None
    assert failed["status"] == "failed"
    assert stale_completion is not None
    assert stale_completion["status"] == "failed"
    assert stale_completion["last_error"] == "terminal failure"
    assert stale_completion["last_error_code"] == "domain_validation_failed"


def test_memory_report_jobs_complete_concurrently_and_shutdown_drains_threads():
    completed: list[str] = []

    def runner(job):
        completed.append(job["session_id"])

    store = InMemoryReportJobStore(runner=runner)
    created = [store.enqueue_report_request(f"s{index}") for index in range(8)]

    store.shutdown(wait=True)

    assert set(completed) == {f"s{index}" for index in range(8)}
    assert all(store.get_job(job["job_id"])["status"] == "completed" for job in created)
    assert store._threads == set()


def test_preview_runtime_factory_completes_report_and_job(monkeypatch):
    import app.services.runtime as runtime
    from app.services.session import InterviewSessionStore
    from tests.unit.test_report_tasks import ReportLLM, finish_session, start_session

    monkeypatch.setenv("INTERVIEW_RUNTIME_STORE", "memory")
    for name in (
        "REPORT_RUNTIME_PROFILE",
        "REPORT_JOB_STORE",
        "REPORT_WORKER",
        "KNOWLEDGE_STORE",
        "EMBEDDING_PROVIDER",
    ):
        monkeypatch.delenv(name, raising=False)

    runtime.reset_runtime_for_tests()
    session_store = InterviewSessionStore(llm=ReportLLM(report_score=83))
    session = start_session(session_store)
    finish_session(session_store, session.session_id)
    runtime.get_runtime_container().set("session_store", session_store)

    queue = runtime.get_report_job_store()
    created = queue.enqueue_report_request(session.session_id)
    runtime.shutdown_runtime(wait=True)

    job = queue.get_job(created["job_id"])
    report = session_store.get_report_record(session.session_id)
    assert job is not None
    assert job["status"] == "completed"
    assert report is not None
    assert report.status == "completed"
    assert report.report is not None
    assert report.report.overall_score == 83
