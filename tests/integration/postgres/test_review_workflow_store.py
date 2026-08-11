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
from app.services.review_execution import bind_review_execution_lease
from app.services.workflow_thread_lock import (
    FencedWriteRejected,
    ReportLeaseLost,
    ReviewEffectBusy,
    ReviewEffectConflict,
    ReviewEffectLeaseLost,
)
from tests.postgres_support import (
    make_runtime_table_prefix,
    require_postgres_dsn as require_dsn,
)


pytestmark = pytest.mark.pg_jobs


@pytest.fixture
def stores():
    dsn = require_dsn()
    prefix = make_runtime_table_prefix("review_store")
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
    job = jobs.claim_next(worker_id="review-worker")
    workflow.initialize_run(job_id=job["job_id"], session_id=turn.session_id, graph_schema_version="langgraph-review-v1", input_sha256="input-1")
    yield session_store, jobs, workflow, turn.session_id, job
    psycopg2, sql = PostgresReportJobStore._import_psycopg2()
    with psycopg2.connect(dsn) as connection:
        with connection.cursor() as cursor:
            for suffix in ("review_effects", "review_artifacts", "review_runs", "runtime_event_receipts", "runtime_outbox", "agent_runs"):
                cursor.execute(sql.SQL("DROP TABLE IF EXISTS {table}").format(table=sql.Identifier(f"{prefix}_{suffix}")))
    PostgresReportJobStore(dsn=dsn, table_prefix=prefix).drop_tables()
    with psycopg2.connect(dsn) as connection:
        with connection.cursor() as cursor:
            for suffix in ("question_evaluations", "messages", "sessions"):
                cursor.execute(sql.SQL("DROP TABLE IF EXISTS {table}").format(table=sql.Identifier(f"{prefix}_{suffix}")))


def make_report(session_id):
    scores = DimensionScores(breadth=70, depth=70, architecture=70, engineering=70, communication=70)
    return InterviewReport(session_id=session_id, overall_score=70, overall_dimension_scores=scores, summary="summary", highlights=["highlight"], feedbacks=[])


def bind_job(job, *, token=None):
    return bind_review_execution_lease(
        job_id=job["job_id"],
        worker_id="review-worker",
        lease_token=token or job["lease_token"],
    )


def test_final_projection_is_idempotent_by_job_and_report_digest(stores):
    session_store, jobs, workflow, session_id, job = stores
    report = make_report(session_id)

    with bind_job(job):
        first = workflow.commit_report(job_id=job["job_id"], report=report)
        second = workflow.commit_report(job_id=job["job_id"], report=report)

    assert first == second
    assert session_store.get(session_id)["state_version"] == first
    assert jobs.get_job(job["job_id"])["status"] == "completed"
    assert session_store.get_report_record(session_id).status == "completed"


def test_changed_report_digest_for_completed_job_fails_closed(stores):
    _, _, workflow, session_id, job = stores
    with bind_job(job):
        workflow.commit_report(job_id=job["job_id"], report=make_report(session_id))
    changed = make_report(session_id).model_copy(update={"summary": "changed"})

    with pytest.raises(ReportCommitConflict):
        workflow.commit_report(job_id=job["job_id"], report=changed)


def test_retry_event_is_idempotent(stores):
    _, _, workflow, _, job = stores

    with bind_job(job):
        first = workflow.schedule_retry(job_id=job["job_id"], next_attempt_number=2, delay_seconds=1)
        second = workflow.schedule_retry(job_id=job["job_id"], next_attempt_number=2, delay_seconds=1)

    assert first == second == f"review-{job['job_id']}-retry-2"
    assert workflow.control.count_outbox(first) == 1


def test_report_artifact_round_trips_outside_checkpoint(stores):
    _, _, workflow, session_id, job = stores
    report = make_report(session_id)

    artifact = workflow.save_report_artifact(job_id=job["job_id"], report=report)

    assert artifact["report_ref"] == f"review-report:{job['job_id']}"
    assert workflow.load_report_artifact(job["job_id"]) == report


def test_stale_report_lease_rolls_back_all_final_projections(stores):
    session_store, jobs, workflow, session_id, job = stores
    before_version = session_store.get(session_id)["state_version"]

    with bind_job(job, token=str(uuid4())):
        with pytest.raises(ReportLeaseLost):
            workflow.commit_report(
                job_id=job["job_id"], report=make_report(session_id)
            )

    assert session_store.get(session_id)["state_version"] == before_version
    assert jobs.get_job(job["job_id"])["status"] == "running"
    assert session_store.get_report_record(session_id).status == "processing"
    assert workflow.get_run(job["job_id"]).status == "running"


def test_completed_review_effect_is_reused_without_provider_call(stores):
    _, _, workflow, _, job = stores
    calls = []

    def provider(ownership):
        ownership.ensure_owned()
        calls.append("called")
        return {"value": "winner"}

    with bind_job(job):
        first = workflow.run_effect(
            operation_key=f"question:{job['job_id']}:q1",
            job_id=job["job_id"],
            effect_type="question_review",
            question_id="q1",
            graph_schema_version="langgraph-review-v1",
            input_sha256="input-q1",
            provider=provider,
        )
        second = workflow.run_effect(
            operation_key=f"question:{job['job_id']}:q1",
            job_id=job["job_id"],
            effect_type="question_review",
            question_id="q1",
            graph_schema_version="langgraph-review-v1",
            input_sha256="input-q1",
            provider=provider,
        )

    assert calls == ["called"]
    assert second == first
    assert second["payload"] == {"value": "winner"}


def test_review_effect_identity_conflict_fails_closed(stores):
    _, _, workflow, _, job = stores
    operation_key = f"report:{job['job_id']}:1"
    with bind_job(job):
        workflow.run_effect(
            operation_key=operation_key,
            job_id=job["job_id"],
            effect_type="report_generation",
            graph_schema_version="langgraph-review-v1",
            input_sha256="input-1",
            provider=lambda ownership: {"value": "one"},
        )
        with pytest.raises(ReviewEffectConflict):
            workflow.run_effect(
                operation_key=operation_key,
                job_id=job["job_id"],
                effect_type="report_generation",
                graph_schema_version="langgraph-review-v1",
                input_sha256="input-2",
                provider=lambda ownership: {"value": "two"},
            )


def test_running_review_effect_returns_busy_without_provider_call(stores):
    _, _, workflow, _, job = stores
    operation_key = f"report:{job['job_id']}:busy"
    with bind_job(job):
        workflow.claim_effect(
            operation_key=operation_key,
            job_id=job["job_id"],
            effect_type="report_generation",
            graph_schema_version="langgraph-review-v1",
            input_sha256="input-1",
        )
        with pytest.raises(ReviewEffectBusy):
            workflow.run_effect(
                operation_key=operation_key,
                job_id=job["job_id"],
                effect_type="report_generation",
                graph_schema_version="langgraph-review-v1",
                input_sha256="input-1",
                provider=lambda ownership: pytest.fail(
                    "provider must not be called"
                ),
            )


def test_expired_effect_claim_is_fenced_after_reclaim(stores):
    _, _, workflow, _, job = stores
    operation_key = f"report:{job['job_id']}:reclaim"
    with bind_job(job):
        first = workflow.claim_effect(
            operation_key=operation_key,
            job_id=job["job_id"],
            effect_type="report_generation",
            graph_schema_version="langgraph-review-v1",
            input_sha256="input-1",
        )
        with workflow.control.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    workflow._sql(
                        """
                        UPDATE {effects}
                        SET claim_expires_at = NOW() - INTERVAL '1 second'
                        WHERE operation_key = %s
                        """
                    ),
                    (operation_key,),
                )
        current = workflow.claim_effect(
            operation_key=operation_key,
            job_id=job["job_id"],
            effect_type="report_generation",
            graph_schema_version="langgraph-review-v1",
            input_sha256="input-1",
        )
        with pytest.raises(FencedWriteRejected):
            workflow.complete_effect(first, {"value": "stale"})
        winner = workflow.complete_effect(current, {"value": "winner"})

    assert current.fencing_version > first.fencing_version
    assert workflow.load_effect_payload(operation_key) == {"value": "winner"}
    assert winner["payload"] == {"value": "winner"}


def test_expired_effect_claim_cannot_be_marked_failed(stores):
    _, _, workflow, _, job = stores
    operation_key = f"report:{job['job_id']}:expired-failure"
    with bind_job(job):
        claim = workflow.claim_effect(
            operation_key=operation_key,
            job_id=job["job_id"],
            effect_type="report_generation",
            graph_schema_version="langgraph-review-v1",
            input_sha256="input-1",
        )
        with workflow.control.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    workflow._sql(
                        """
                        UPDATE {effects}
                        SET claim_expires_at = NOW() - INTERVAL '1 second'
                        WHERE operation_key = %s
                        """
                    ),
                    (operation_key,),
                )

        with pytest.raises(ReviewEffectLeaseLost):
            workflow.fail_effect(claim)


def test_reclaimed_effect_cannot_be_marked_failed_by_stale_claim(stores):
    _, _, workflow, _, job = stores
    operation_key = f"report:{job['job_id']}:stale-failure"
    with bind_job(job):
        stale = workflow.claim_effect(
            operation_key=operation_key,
            job_id=job["job_id"],
            effect_type="report_generation",
            graph_schema_version="langgraph-review-v1",
            input_sha256="input-1",
        )
        with workflow.control.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    workflow._sql(
                        """
                        UPDATE {effects}
                        SET claim_expires_at = NOW() - INTERVAL '1 second'
                        WHERE operation_key = %s
                        """
                    ),
                    (operation_key,),
                )
        current = workflow.claim_effect(
            operation_key=operation_key,
            job_id=job["job_id"],
            effect_type="report_generation",
            graph_schema_version="langgraph-review-v1",
            input_sha256="input-1",
        )

        with pytest.raises(ReviewEffectLeaseLost):
            workflow.fail_effect(stale)
        workflow.fail_effect(current)


def test_effect_failure_requires_active_report_job_lease(stores):
    _, _, workflow, _, job = stores
    operation_key = f"report:{job['job_id']}:job-lease-failure"
    with bind_job(job):
        claim = workflow.claim_effect(
            operation_key=operation_key,
            job_id=job["job_id"],
            effect_type="report_generation",
            graph_schema_version="langgraph-review-v1",
            input_sha256="input-1",
        )

    with bind_job(job, token=str(uuid4())):
        with pytest.raises(ReportLeaseLost):
            workflow.fail_effect(claim)
