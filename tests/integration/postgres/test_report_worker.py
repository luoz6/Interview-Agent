"""PostgreSQL integration coverage for the durable report worker."""

from types import SimpleNamespace

import pytest

from app.services.postgres_session import PostgresInterviewSessionStore
from app.services.prep import InterviewPlan, InterviewQuestion
from app.services.report import InterviewReport
from app.services.report_jobs import PostgresReportJobStore
from app.services.report_worker import run_one_job
from tests.postgres_support import (
    drop_runtime_tables,
    make_runtime_table_prefix,
    require_postgres_dsn,
)
from tests.report_worker_fixtures import make_report


def make_table_prefix() -> str:
    return make_runtime_table_prefix("worker")


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
    def search(
        self,
        query_text: str,
        *,
        job_tags: list[str],
        source_types=None,
        limit=5,
    ):
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
    dsn = require_postgres_dsn()
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
        store.submit_answer(
            turn.session_id,
            "I used cache-aside and database fallback.",
        )
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
        assert result["status"] == "completed", result
        assert job_store.get_job(queued_job["job_id"])["status"] == "completed"
        report_record = store.get_report_record(turn.session_id)
        assert report_record is not None
        assert report_record.status == "completed"
        assert report_record.report is not None
        assert report_record.report.overall_score == 81
    finally:
        drop_runtime_tables(dsn, table_prefix)
