import logging
import os
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.services.postgres_session import PostgresInterviewSessionStore
from app.services.prep import InterviewPlan, InterviewQuestion
from app.services.report_jobs import PostgresReportJobStore
from app.services.report import (
    DimensionScores,
    InterviewFeedback,
    InterviewReport,
    ReportGenerationFailed,
    ReportGenerationTimeout,
    ReportQualityFailed,
)
from app.services.report_worker import run_one_job


class FakeJobStore:
    def __init__(self, claimed_job: dict | None) -> None:
        self.claimed_job = claimed_job
        self.repair_calls = 0
        self.claim_calls: list[str] = []
        self.completed_calls: list[str] = []
        self.retry_calls: list[tuple[str, str]] = []
        self.failed_calls: list[tuple[str, str]] = []

    def repair_orphan_processing_reports(self) -> int:
        self.repair_calls += 1
        return 0

    def claim_next(self, worker_id: str) -> dict | None:
        self.claim_calls.append(worker_id)
        return self.claimed_job

    def mark_completed(self, job_id: str) -> dict:
        self.completed_calls.append(job_id)
        return {
            "job_id": job_id,
            "session_id": self.claimed_job["session_id"],
            "status": "completed",
        }

    def mark_retryable_failure(self, job_id: str, error: str) -> dict:
        self.retry_calls.append((job_id, error))
        return {
            "job_id": job_id,
            "session_id": self.claimed_job["session_id"],
            "status": "retrying",
            "last_error": error,
        }

    def mark_failed(self, job_id: str, error: str) -> dict:
        self.failed_calls.append((job_id, error))
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


def make_report(session_id: str = "s1") -> InterviewReport:
    return InterviewReport(
        session_id=session_id,
        overall_score=81,
        overall_dimension_scores=DimensionScores(
            breadth=81,
            depth=81,
            architecture=81,
            engineering=81,
            communication=81,
        ),
        summary="候选人展示了扎实的后端基础，并能说明缓存与数据库兜底的核心取舍。",
        highlights=["Explained tradeoffs"],
        feedbacks=[
            InterviewFeedback(
                question_id="q1",
                question_text="Introduce a backend project.",
                user_answer="I built a cache service.",
                score=81,
                dimension_scores=DimensionScores(
                    breadth=81,
                    depth=81,
                    architecture=81,
                    engineering=81,
                    communication=81,
                ),
                rationale="回答说明了缓存服务的实现细节，并覆盖了 Redis 与数据库兜底路径。",
                critique="还需要补充更清晰的线上指标，例如延迟、错误率和恢复时间。",
                better_answer="我通过 Redis 缓存和数据库兜底降低 p95 延迟，并监控缓存失效时的降级表现。",
                references=[],
            )
        ],
    )


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
    assert store.failed_reports == [
        (
            "s1",
            "runtime report quality check failed: summary must include Simplified Chinese text",
        )
    ]


def require_dsn():
    dsn = os.getenv("POSTGRES_DSN")
    if not dsn:
        pytest.skip("POSTGRES_DSN is required for pg_jobs tests")
    return dsn


def make_table_prefix() -> str:
    return "test_worker_" + uuid4().hex[:12]


def drop_runtime_tables(dsn: str, table_prefix: str) -> None:
    psycopg2, sql = PostgresReportJobStore._import_psycopg2()
    table_names = [
        f"{table_prefix}_question_evaluations",
        f"{table_prefix}_report_jobs",
        f"{table_prefix}_reports",
        f"{table_prefix}_messages",
        f"{table_prefix}_sessions",
    ]
    with psycopg2.connect(dsn) as connection:
        with connection.cursor() as cursor:
            for table_name in table_names:
                cursor.execute(
                    sql.SQL("DROP TABLE IF EXISTS {table} CASCADE").format(
                        table=sql.Identifier(table_name)
                    )
                )


class PostgresWorkerLLM:
    def generate_plan(self, job_description: str, resume_text: str) -> InterviewPlan:
        raise AssertionError("worker integration test does not generate plans")

    def generate_followup(self, context: list[dict[str, str]]) -> str:
        return "Please explain the tradeoffs."

    def generate_report(
        self,
        plan: InterviewPlan,
        evaluation_items: list[dict],
        session_id: str,
    ) -> InterviewReport:
        return make_report(session_id)


class PostgresWorkerVectorStore:
    def search(self, query_text: str, *, job_tags: list[str], source_types=None, limit=5):
        return []


def make_plan() -> InterviewPlan:
    return InterviewPlan(
        title="Backend mock interview",
        questions=[
            InterviewQuestion(
                id="q1",
                kind="project",
                prompt="Introduce a backend project.",
                focus="project depth",
            )
        ],
    )


@pytest.mark.pg_jobs
def test_run_one_job_completes_postgres_job_and_report():
    dsn = require_dsn()
    table_prefix = make_table_prefix()
    try:
        store = PostgresInterviewSessionStore(
            dsn=dsn,
            table_prefix=table_prefix,
            llm=PostgresWorkerLLM(),
        )
        job_store = PostgresReportJobStore(dsn=dsn, table_prefix=table_prefix)

        turn = store.start(
            make_plan(),
            job_description="Backend role using Python and Redis.",
            resume_text="Built a Python API with Redis.",
            job_tags=["python", "redis"],
        )
        store.submit_answer(turn.session_id, "I built a Redis-backed service.")
        store.submit_answer(turn.session_id, "I used cache-aside and database fallback.")
        queued_job = job_store.enqueue_report_request(turn.session_id)

        executor = SimpleNamespace(
            store=store,
            llm=store.llm,
            vector_store=PostgresWorkerVectorStore(),
        )

        result = run_one_job(
            job_store=job_store,
            executor=executor,
            worker_id="worker-1",
        )

        assert result is not None
        assert result["status"] == "completed"
        assert job_store.get_job(queued_job["job_id"])["status"] == "completed"
        report_record = store.get_report_record(turn.session_id)
        assert report_record is not None
        assert report_record.status == "completed"
        assert report_record.report is not None
        assert report_record.report.overall_score == 81
    finally:
        drop_runtime_tables(dsn, table_prefix)
