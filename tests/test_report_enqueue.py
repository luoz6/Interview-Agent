from pathlib import Path

from app.services.report import ReportProgress, ReportRecord
from app.services.report_enqueue import enqueue_report_if_needed


class FakeStore:
    def __init__(self, *, existing_record=None, mark_result=True):
        self.existing_record = existing_record
        self.mark_result = mark_result
        self.marked = []
        self.failed = []

    def get_report_record(self, session_id):
        return self.existing_record

    def mark_report_processing(self, session_id):
        self.marked.append(session_id)
        return self.mark_result

    def fail_report(self, session_id, error):
        self.failed.append((session_id, error))


class FakeJobStore:
    def __init__(self, *, error=None):
        self.error = error
        self.enqueued = []

    def enqueue_report_request(self, session_id):
        if self.error is not None:
            raise self.error
        self.enqueued.append(session_id)
        return {
            "job_id": "job-1",
            "session_id": session_id,
            "status": "queued",
        }


class FakeBackgroundTasks:
    def __init__(self):
        self.tasks = []

    def add_task(self, func, *args):
        self.tasks.append((func, args))


def test_enqueue_report_ignores_active_turns():
    store = FakeStore()
    job_store = FakeJobStore()
    background_tasks = FakeBackgroundTasks()

    enqueue_report_if_needed(
        turn_status="active",
        session_id="s1",
        store=store,
        job_store=job_store,
        background_tasks=background_tasks,
    )

    assert job_store.enqueued == []
    assert store.marked == []
    assert background_tasks.tasks == []


def test_enqueue_report_ignores_sessions_with_existing_report_record():
    existing = ReportRecord(
        status="processing",
        progress=ReportProgress(stage="retrieving", percent=20, message="Retrieving."),
    )
    store = FakeStore(existing_record=existing)
    job_store = FakeJobStore()

    enqueue_report_if_needed(
        turn_status="finished",
        session_id="s1",
        store=store,
        job_store=job_store,
        background_tasks=FakeBackgroundTasks(),
    )

    assert job_store.enqueued == []


def test_enqueue_report_uses_job_store_for_finished_sessions():
    store = FakeStore()
    job_store = FakeJobStore()

    enqueue_report_if_needed(
        turn_status="finished",
        session_id="s1",
        store=store,
        job_store=job_store,
        background_tasks=FakeBackgroundTasks(),
    )

    assert job_store.enqueued == ["s1"]


def test_enqueue_report_marks_failed_without_background_fallback_when_queue_unavailable():
    store = FakeStore()
    job_store = FakeJobStore(error=RuntimeError("postgres queue unavailable"))
    background_tasks = FakeBackgroundTasks()

    result = enqueue_report_if_needed(
        turn_status="finished",
        session_id="s1",
        store=store,
        job_store=job_store,
        background_tasks=background_tasks,
    )

    assert result.status == "failed"
    assert result.error_code == "report_enqueue_unavailable"
    assert result.retryable is True
    assert store.marked == []
    assert store.failed == [("s1", "report queue unavailable")]
    assert background_tasks.tasks == []


def test_enqueue_report_marks_failed_for_database_style_exceptions():
    store = FakeStore()
    job_store = FakeJobStore(error=ConnectionError("database unavailable"))
    background_tasks = FakeBackgroundTasks()

    result = enqueue_report_if_needed(
        turn_status="finished",
        session_id="s1",
        store=store,
        job_store=job_store,
        background_tasks=background_tasks,
    )

    assert result.status == "failed"
    assert store.marked == []
    assert store.failed == [("s1", "report queue unavailable")]
    assert background_tasks.tasks == []


def test_enqueue_report_marks_failed_when_job_store_factory_fails():
    store = FakeStore()
    background_tasks = FakeBackgroundTasks()

    result = enqueue_report_if_needed(
        turn_status="finished",
        session_id="s1",
        store=store,
        job_store_factory=lambda: (_ for _ in ()).throw(ConnectionError("queue init failed")),
        background_tasks=background_tasks,
    )

    assert result.status == "failed"
    assert store.marked == []
    assert store.failed == [("s1", "report queue unavailable")]
    assert background_tasks.tasks == []


def test_enqueue_report_returns_durable_job_identity():
    store = FakeStore()
    job_store = FakeJobStore()

    result = enqueue_report_if_needed(
        turn_status="finished",
        session_id="s1",
        store=store,
        job_store=job_store,
        background_tasks=FakeBackgroundTasks(),
    )

    assert result.status == "queued"
    assert result.job_id == "job-1"
    assert result.error_code is None
    assert result.retryable is False


def test_api_routes_do_not_import_report_task_executor_directly():
    routes_source = Path("app/api/routes.py").read_text(encoding="utf-8")

    assert "generate_report_for_session" not in routes_source
    assert "report_tasks" not in routes_source
    assert "enqueue_report_if_needed" in routes_source
