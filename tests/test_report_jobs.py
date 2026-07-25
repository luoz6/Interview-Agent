from uuid import uuid4

import pytest

from app.services.postgres_session import PostgresInterviewSessionStore
from app.services.prep import InterviewPlan, InterviewQuestion
from app.services.report_jobs import PostgresReportJobStore
from tests.postgres_support import require_postgres_dsn as require_dsn


pytestmark = pytest.mark.pg_jobs


def make_table_prefix():
    return "test_jobs_" + uuid4().hex[:12]


def make_plan() -> InterviewPlan:
    return InterviewPlan(
        title="Backend Interview",
        questions=[
            InterviewQuestion(
                id="q1",
                kind="project",
                prompt="Describe your backend project.",
                focus="Project depth",
            )
        ],
    )


def list_tables(dsn: str, *table_names: str) -> list[str]:
    psycopg2 = PostgresReportJobStore._import_psycopg2()[0]
    with psycopg2.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'public'
                  AND table_name = ANY(%s)
                ORDER BY table_name
                """,
                (list(table_names),),
            )
            return [row[0] for row in cursor.fetchall()]


def drop_runtime_tables(dsn: str, table_prefix: str) -> None:
    psycopg2, sql = PostgresReportJobStore._import_psycopg2()
    table_names = [
        f"{table_prefix}_runtime_event_receipts",
        f"{table_prefix}_agent_runs",
        f"{table_prefix}_runtime_outbox",
        f"{table_prefix}_report_jobs",
        f"{table_prefix}_reports",
        f"{table_prefix}_question_evaluations",
        f"{table_prefix}_messages",
        f"{table_prefix}_sessions",
    ]
    with psycopg2.connect(dsn) as connection:
        with connection.cursor() as cursor:
            for table_name in table_names:
                cursor.execute(
                    sql.SQL("DROP TABLE IF EXISTS {table}").format(
                        table=sql.Identifier(table_name)
                    )
                )


@pytest.fixture
def stores():
    dsn = require_dsn()
    table_prefix = make_table_prefix()
    session_store = PostgresInterviewSessionStore(dsn=dsn, table_prefix=table_prefix)
    job_store = PostgresReportJobStore(dsn=dsn, table_prefix=table_prefix)
    yield {
        "dsn": dsn,
        "table_prefix": table_prefix,
        "session_store": session_store,
        "job_store": job_store,
    }
    drop_runtime_tables(dsn, table_prefix)
    assert (
        list_tables(
            dsn,
            f"{table_prefix}_report_jobs",
            f"{table_prefix}_reports",
            f"{table_prefix}_messages",
            f"{table_prefix}_sessions",
        )
        == []
    )


def create_session(session_store: PostgresInterviewSessionStore) -> str:
    turn = session_store.start(
        make_plan(),
        job_description="Python backend role",
        resume_text="Built FastAPI services",
        job_tags=["python", "fastapi"],
    )
    return turn.session_id


def test_enqueue_report_request_creates_job_and_processing_report(stores):
    session_id = create_session(stores["session_store"])
    created = stores["job_store"].enqueue_report_request(session_id=session_id)

    job = stores["job_store"].get_job_by_session(session_id)
    report = stores["job_store"].get_report_row(session_id)

    assert created["session_id"] == session_id
    assert job["status"] == "queued"
    assert report["status"] == "processing"
    assert stores["job_store"].count_jobs() == 1
    assert stores["job_store"].count_reports() == 1


def seed_running_report(stores):
    session_id = create_session(stores["session_store"])
    stores["job_store"].enqueue_report_request(
        session_id=session_id
    )
    job = stores["job_store"].claim_next(worker_id="worker-1")
    return session_id, job["job_id"]


def test_report_failure_persists_stable_error_code(stores):
    session_id, job_id = seed_running_report(stores)

    stores["job_store"].mark_failed(
        job_id,
        "internal detail",
        error_code="domain_validation_failed",
    )
    job = stores["job_store"].get_job_by_session(session_id)

    assert job["last_error_code"] == "domain_validation_failed"


def test_report_requeue_updates_job_and_report(stores):
    session_id, job_id = seed_running_report(stores)
    stores["job_store"].mark_failed(
        job_id,
        "internal detail",
        error_code="domain_validation_failed",
    )

    job = stores["job_store"].requeue_failed(session_id)
    report = stores["job_store"].get_report_row(session_id)

    assert job["job_id"] == job_id
    assert job["status"] == "queued"
    assert job["attempt_count"] == 0
    assert job["last_error_code"] is None
    assert job["replay_count"] == 1
    assert report["status"] == "processing"


def test_report_requeue_rejects_second_attempt_without_mutating_replay_count(stores):
    session_id, job_id = seed_running_report(stores)
    stores["job_store"].mark_failed(
        job_id,
        "internal detail",
        error_code="domain_validation_failed",
    )
    stores["job_store"].requeue_failed(session_id)

    with pytest.raises(ValueError, match="report job is not failed"):
        stores["job_store"].requeue_failed(session_id)

    job = stores["job_store"].get_job_by_session(session_id)
    assert job["status"] == "queued"
    assert job["review_engine"] == "legacy"
    assert job["review_graph_schema_version"] is None
    assert job["replay_count"] == 1


def test_enqueue_report_request_is_idempotent_for_same_session(stores):
    session_id = create_session(stores["session_store"])
    first = stores["job_store"].enqueue_report_request(session_id=session_id)
    second = stores["job_store"].enqueue_report_request(session_id=session_id)

    assert first["job_id"] == second["job_id"]
    assert first["review_engine"] == second["review_engine"]
    assert (
        first["review_graph_schema_version"]
        == second["review_graph_schema_version"]
    )
    assert stores["job_store"].count_jobs() == 1
    assert stores["job_store"].count_reports() == 1


def test_enqueue_existing_failed_job_does_not_reset_assignment_or_status(stores):
    session_id, job_id = seed_running_report(stores)
    stores["job_store"].mark_failed(
        job_id,
        "internal detail",
        error_code="domain_validation_failed",
    )

    returned = stores["job_store"].enqueue_report_request(session_id)
    job = stores["job_store"].get_job_by_session(session_id)
    report = stores["job_store"].get_report_row(session_id)

    assert returned["job_id"] == job_id
    assert job["status"] == "failed"
    assert report["status"] == "failed"


def test_requeue_preserves_immutable_review_assignment(stores, monkeypatch):
    monkeypatch.setenv("REPORT_LANGGRAPH_ROLLOUT_PERCENT", "100")
    session_id, _ = seed_running_report(stores)
    first = stores["job_store"].get_job_by_session(session_id)
    stores["job_store"].mark_failed(
        first["job_id"],
        "internal detail",
        error_code="domain_validation_failed",
    )

    replayed = stores["job_store"].requeue_failed(session_id)

    assert first["review_engine"] == "langgraph-review-v1"
    assert first["review_graph_schema_version"] == "langgraph-review-v1"
    assert replayed["review_engine"] == first["review_engine"]
    assert (
        replayed["review_graph_schema_version"]
        == first["review_graph_schema_version"]
    )


def test_claim_marks_job_running(stores):
    session_id = create_session(stores["session_store"])
    stores["job_store"].enqueue_report_request(session_id=session_id)

    claimed = stores["job_store"].claim_next(worker_id="worker-1")

    assert claimed is not None
    assert claimed["status"] == "running"
    assert claimed["lease_owner"] == "worker-1"
    assert claimed["lease_token"]
    assert stores["job_store"].assert_lease(
        claimed["job_id"],
        worker_id="worker-1",
        lease_token=claimed["lease_token"],
    )


def test_report_lease_token_fences_stale_worker(stores):
    session_id = create_session(stores["session_store"])
    stores["job_store"].enqueue_report_request(session_id=session_id)
    first = stores["job_store"].claim_next(
        worker_id="worker-1", lease_seconds=-1
    )
    second = stores["job_store"].claim_next(worker_id="worker-2")

    assert first["lease_token"] != second["lease_token"]
    assert not stores["job_store"].heartbeat(
        first["job_id"],
        worker_id="worker-1",
        lease_token=first["lease_token"],
    )
    assert stores["job_store"].heartbeat(
        second["job_id"],
        worker_id="worker-2",
        lease_token=second["lease_token"],
    )


def test_expired_running_job_can_be_reclaimed(stores):
    session_id = create_session(stores["session_store"])
    stores["job_store"].enqueue_report_request(session_id=session_id)
    first = stores["job_store"].claim_next(worker_id="worker-1", lease_seconds=-1)

    reclaimed = stores["job_store"].claim_next(worker_id="worker-2")

    assert first is not None
    assert reclaimed is not None
    assert reclaimed["session_id"] == session_id
    assert reclaimed["lease_owner"] == "worker-2"
    assert reclaimed["status"] == "running"


def test_retryable_failure_marks_retrying_until_max_attempts(stores):
    session_id = create_session(stores["session_store"])
    created = stores["job_store"].enqueue_report_request(session_id=session_id)

    stores["job_store"].mark_retryable_failure(created["job_id"], "transient error")
    current = stores["job_store"].get_job(created["job_id"])
    assert current["status"] == "retrying"

    stores["job_store"].mark_retryable_failure(created["job_id"], "transient error")
    stores["job_store"].mark_retryable_failure(created["job_id"], "transient error")
    terminal = stores["job_store"].get_job(created["job_id"])
    assert terminal["status"] == "failed"


def test_repair_orphan_processing_reports_enqueues_missing_job(stores):
    session_id = create_session(stores["session_store"])
    stores["job_store"].insert_processing_report_only(session_id=session_id)

    repaired = stores["job_store"].repair_orphan_processing_reports()
    job = stores["job_store"].get_job_by_session(session_id)

    assert repaired == 1
    assert job is not None
    assert job["status"] == "queued"


def test_deleting_session_cascades_to_report_and_job_rows(stores):
    dsn = stores["dsn"]
    session_store = stores["session_store"]
    job_store = stores["job_store"]
    session_id = create_session(session_store)
    job_store.enqueue_report_request(session_id=session_id)

    psycopg2, sql = PostgresReportJobStore._import_psycopg2()
    with psycopg2.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                sql.SQL("DELETE FROM {sessions} WHERE session_id = %s").format(
                    sessions=sql.Identifier(session_store.sessions_table)
                ),
                (session_id,),
            )

    with pytest.raises(ValueError, match="session not found"):
        session_store.get(session_id)
    assert job_store.get_report_row(session_id) is None
    assert job_store.get_job_by_session(session_id) is None
