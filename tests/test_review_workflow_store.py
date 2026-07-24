import os
from uuid import uuid4

import pytest

from app.services.postgres_session import PostgresInterviewSessionStore
from app.services.prep import InterviewPlan, InterviewQuestion
from app.services.report import DimensionScores, InterviewReport
from app.services.report_jobs import PostgresReportJobStore
from app.services.review_workflow_store import (
    PostgresReviewWorkflowStore,
    ReportCommitConflict,
)


pytestmark = pytest.mark.pg_jobs


def require_dsn():
    dsn = os.getenv("POSTGRES_DSN")
    if not dsn:
        pytest.skip("POSTGRES_DSN is required")
    return dsn


@pytest.fixture
def stores():
    dsn = require_dsn()
    prefix = "test_review_store_" + uuid4().hex[:12]
    session_store = PostgresInterviewSessionStore(dsn=dsn, table_prefix=prefix)
    jobs = PostgresReportJobStore(dsn=dsn, table_prefix=prefix)
    workflow = PostgresReviewWorkflowStore(dsn=dsn, table_prefix=prefix)
    turn = session_store.start(
        InterviewPlan(title="Backend", questions=[InterviewQuestion(id="q1", kind="project", prompt="Prompt", focus="focus")]),
        job_description="role",
        resume_text="resume",
        job_tags=["python"],
    )
    session_store.finish(turn.session_id)
    job = jobs.enqueue_report_request(turn.session_id)
    workflow.initialize_run(job_id=job["job_id"], session_id=turn.session_id, graph_schema_version="langgraph-review-v1", input_sha256="input-1")
    yield session_store, jobs, workflow, turn.session_id, job
    psycopg2, sql = PostgresReportJobStore._import_psycopg2()
    with psycopg2.connect(dsn) as connection:
        with connection.cursor() as cursor:
            for suffix in ("review_runs", "runtime_event_receipts", "runtime_outbox", "agent_runs"):
                cursor.execute(sql.SQL("DROP TABLE IF EXISTS {table}").format(table=sql.Identifier(f"{prefix}_{suffix}")))
    PostgresReportJobStore(dsn=dsn, table_prefix=prefix).drop_tables()
    with psycopg2.connect(dsn) as connection:
        with connection.cursor() as cursor:
            for suffix in ("question_evaluations", "messages", "sessions"):
                cursor.execute(sql.SQL("DROP TABLE IF EXISTS {table}").format(table=sql.Identifier(f"{prefix}_{suffix}")))


def make_report(session_id):
    scores = DimensionScores(breadth=70, depth=70, architecture=70, engineering=70, communication=70)
    return InterviewReport(session_id=session_id, overall_score=70, overall_dimension_scores=scores, summary="summary", highlights=["highlight"], feedbacks=[])


def test_final_projection_is_idempotent_by_job_and_report_digest(stores):
    session_store, jobs, workflow, session_id, job = stores
    report = make_report(session_id)

    first = workflow.commit_report(job_id=job["job_id"], report=report)
    second = workflow.commit_report(job_id=job["job_id"], report=report)

    assert first == second
    assert session_store.get(session_id)["state_version"] == first
    assert jobs.get_job(job["job_id"])["status"] == "completed"
    assert session_store.get_report_record(session_id).status == "completed"


def test_changed_report_digest_for_completed_job_fails_closed(stores):
    _, _, workflow, session_id, job = stores
    workflow.commit_report(job_id=job["job_id"], report=make_report(session_id))
    changed = make_report(session_id).model_copy(update={"summary": "changed"})

    with pytest.raises(ReportCommitConflict):
        workflow.commit_report(job_id=job["job_id"], report=changed)


def test_retry_event_is_idempotent(stores):
    _, _, workflow, _, job = stores

    first = workflow.schedule_retry(job_id=job["job_id"], next_attempt_number=2, delay_seconds=1)
    second = workflow.schedule_retry(job_id=job["job_id"], next_attempt_number=2, delay_seconds=1)

    assert first == second == f"review-{job['job_id']}-retry-2"
    assert workflow.control.count_outbox(first) == 1
