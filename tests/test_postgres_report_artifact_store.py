import json
from uuid import uuid4

import pytest

from app.services.postgres_report_artifact_store import PostgresReportArtifactStore
from app.services.report_artifact_store import ReportArtifactConflict
from app.services.review_workflow_store import PostgresReviewWorkflowStore
from app.services.postgres_session import PostgresInterviewSessionStore
from app.services.report_jobs import PostgresReportJobStore
from app.services.prep import InterviewPlan, InterviewQuestion
from tests.postgres_support import require_postgres_dsn


pytestmark = pytest.mark.pg_runtime


def prefix():
    return "test_artifact_v2_" + uuid4().hex[:10]


def make_plan():
    return InterviewPlan(
        title="Artifact test interview",
        questions=[
            InterviewQuestion(id="q1", kind="technical", prompt="Explain caching.", focus="cache"),
            InterviewQuestion(id="q2", kind="project", prompt="Describe the project.", focus="project"),
            InterviewQuestion(id="q3", kind="system-design", prompt="Design the service.", focus="design"),
        ],
    )


def payload(score_status="scored"):
    from app.services.report_artifact import PublishReportArtifact

    return PublishReportArtifact(
        schema_version="report-artifact-v2",
        scoring_rubric_version="rubric-v1",
        generation_status="complete",
        generation_reason_code="normal",
        score_status=score_status,
        score_reason_code="sufficient_evidence"
        if score_status == "scored"
        else "insufficient_evidence",
        coverage_status="complete" if score_status == "scored" else "none",
        report_path="full_session",
        payload={"overall_score": 84 if score_status == "scored" else None},
    )


def make_stores(failure_injector=None):
    dsn = require_postgres_dsn()
    table_prefix = prefix()
    sessions = PostgresInterviewSessionStore(dsn=dsn, table_prefix=table_prefix)
    session = sessions.start(
        make_plan(),
        job_description="Backend role",
        resume_text="Built APIs",
        job_tags=["backend"],
    )
    artifacts = PostgresReportArtifactStore(
        dsn=dsn,
        table_prefix=table_prefix,
        failure_injector=failure_injector,
    )
    return sessions, artifacts, session.session_id


def test_postgres_artifact_history_active_pointer_and_failed_requeue():
    _, store, session_id = make_stores()
    first_job = store.enqueue_job(session_id=session_id, idempotency_key="initial-1")
    first_job = store.claim_job(first_job.job_id, worker_id="worker-1")
    first = store.publish(first_job.job_id, payload(), worker_id="worker-1")
    assert store.publish(first_job.job_id, payload(), worker_id="worker-1").report_id == first.report_id

    rescore = store.enqueue_job(
        session_id=session_id,
        job_kind="rescore",
        source_report_id=first.report_id,
        idempotency_key="rescore-1",
    )
    failed = store.fail_job(rescore.job_id, error_code="provider_timeout")
    assert failed.status == "failed"
    assert store.get_head(session_id).active_report_id == first.report_id
    assert store.requeue_failed(rescore.job_id).status == "queued"
    assert [item.revision for item in store.list_artifacts(session_id)] == [1]


def test_postgres_publish_replays_after_response_loss_by_source_job_and_hash():
    _, store, session_id = make_stores()
    job = store.claim_job(
        store.enqueue_job(
            session_id=session_id,
            idempotency_key="response-lost",
        ).job_id,
        worker_id="worker-1",
    )
    committed = store.publish(job.job_id, payload(), worker_id="worker-1")

    recovered = PostgresReportArtifactStore(
        dsn=require_postgres_dsn(),
        table_prefix=store.table_prefix,
        schema_mode="migrate",
    )
    replay = recovered.publish(job.job_id, payload(), worker_id="worker-1")

    assert replay == committed
    assert replay.source_job_id == job.job_id
    assert replay.artifact_sha256 == committed.artifact_sha256
    assert recovered.list_artifacts(session_id) == [committed]
    assert recovered.list_jobs(session_id)[0].status == "completed"
    with pytest.raises(ReportArtifactConflict, match="payload conflicts"):
        recovered.publish(
            job.job_id,
            payload().model_copy(
                update={"payload": {"overall_score": 12}}
            ),
            worker_id="worker-1",
        )


def test_postgres_multiple_rescores_keep_history_and_one_active_job():
    _, store, session_id = make_stores()
    initial_job = store.claim_job(
        store.enqueue_job(
            session_id=session_id,
            idempotency_key="initial-history",
        ).job_id,
        worker_id="worker-1",
    )
    first = store.publish(initial_job.job_id, payload(), worker_id="worker-1")

    rescore_one = store.enqueue_job(
        session_id=session_id,
        job_kind="rescore",
        source_report_id=first.report_id,
        idempotency_key="rescore-history-1",
    )
    with pytest.raises(
        ReportArtifactConflict,
        match="session already has an active report job",
    ):
        store.enqueue_job(
            session_id=session_id,
            job_kind="rescore",
            source_report_id=first.report_id,
            idempotency_key="rescore-history-overlap",
        )
    rescore_one = store.claim_job(rescore_one.job_id, worker_id="worker-1")
    second = store.publish(
        rescore_one.job_id,
        payload(score_status="unscored"),
        worker_id="worker-1",
    )

    rescore_two = store.claim_job(
        store.enqueue_job(
            session_id=session_id,
            job_kind="rescore",
            source_report_id=second.report_id,
            idempotency_key="rescore-history-2",
        ).job_id,
        worker_id="worker-2",
    )
    third = store.publish(
        rescore_two.job_id,
        payload(),
        worker_id="worker-2",
    )

    artifacts = store.list_artifacts(session_id)
    assert [item.revision for item in artifacts] == [1, 2, 3]
    assert [item.source_report_id for item in artifacts] == [
        None,
        first.report_id,
        second.report_id,
    ]
    assert store.get_head(session_id).active_report_id == third.report_id
    assert all(job.status == "completed" for job in store.list_jobs(session_id))


@pytest.mark.parametrize(
    "step",
    ["before_artifact", "artifact", "head", "job", "review_run", "session"],
)
def test_postgres_artifact_publish_rolls_back_all_steps(step):
    def failure(current):
        if current == step:
            raise RuntimeError("injected failure")

    _, store, session_id = make_stores(failure)
    job = store.enqueue_job(session_id=session_id, idempotency_key=f"failure-{step}")
    job = store.claim_job(job.job_id, worker_id="worker-1")

    with pytest.raises(RuntimeError):
        store.publish(job.job_id, payload(), worker_id="worker-1")

    assert store.list_artifacts(session_id) == []
    assert store.get_head(session_id).active_report_id is None
    assert store.list_jobs(session_id)[0].status == "running"


def test_postgres_artifact_rejects_cross_session_source_and_head():
    sessions, store, session_id = make_stores()
    first_job = store.claim_job(
        store.enqueue_job(session_id=session_id, idempotency_key="first").job_id,
        worker_id="worker-1",
    )
    first = store.publish(first_job.job_id, payload(), worker_id="worker-1")
    other = sessions.start(
        make_plan(),
        job_description="Another backend role",
        resume_text="Built workers",
        job_tags=["backend"],
    )

    with pytest.raises(ReportArtifactConflict, match="does not belong"):
        store.enqueue_job(
            session_id=other.session_id,
            job_kind="rescore",
            source_report_id=first.report_id,
            idempotency_key="cross-session",
        )

    psycopg2, sql = store._import_psycopg2()
    with psycopg2.connect(require_postgres_dsn()) as connection:
        with connection.cursor() as cursor:
            with pytest.raises(psycopg2.Error, match="active report must belong"):
                cursor.execute(
                    sql.SQL(
                        "INSERT INTO {heads}(session_id,active_report_id,updated_at) "
                        "VALUES(%s,%s::uuid,NOW()) ON CONFLICT(session_id) DO UPDATE "
                        "SET active_report_id=EXCLUDED.active_report_id"
                    ).format(heads=sql.Identifier(store.heads_table)),
                    (other.session_id, first.report_id),
                )


def test_postgres_publish_completes_review_run_in_same_transaction():
    _, store, session_id = make_stores()
    workflow = PostgresReviewWorkflowStore(
        dsn=require_postgres_dsn(),
        table_prefix=store.table_prefix,
    )
    job = store.enqueue_job(session_id=session_id, idempotency_key="review-run")
    psycopg2, sql = store._import_psycopg2()
    with psycopg2.connect(require_postgres_dsn()) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                sql.SQL("UPDATE {jobs} SET review_engine='langgraph-review-v1' WHERE job_id=%s::uuid").format(
                    jobs=sql.Identifier(store.jobs_table)
                ),
                (job.job_id,),
            )
    workflow.initialize_run(
        job_id=job.job_id,
        session_id=session_id,
        graph_schema_version="review-v1",
        input_sha256="a" * 64,
    )
    claimed = store.claim_job(job.job_id, worker_id="worker-1")
    artifact = store.publish(claimed.job_id, payload(), worker_id="worker-1")

    run = workflow.get_run(job.job_id)
    assert run.status == "completed"
    assert run.result_sha256 == artifact.artifact_sha256


def test_postgres_legacy_report_promotion_is_additive_and_idempotent():
    dsn = require_postgres_dsn()
    table_prefix = prefix()
    sessions = PostgresInterviewSessionStore(dsn=dsn, table_prefix=table_prefix)
    session = sessions.start(
        make_plan(),
        job_description="Legacy role",
        resume_text="Legacy resume",
        job_tags=["legacy"],
    )
    jobs = PostgresReportJobStore(dsn=dsn, table_prefix=table_prefix)
    job = jobs.enqueue_report_request(session.session_id)
    psycopg2, sql = jobs._import_psycopg2()
    legacy_payload = {"overall_score": 73, "overall_dimension_scores": {"depth": 73}}
    with psycopg2.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                sql.SQL(
                    "UPDATE {reports} SET status='completed',report_json=%s::jsonb "
                    "WHERE session_id=%s"
                ).format(reports=sql.Identifier(f"{table_prefix}_reports")),
                (json.dumps(legacy_payload), session.session_id),
            )
    store = PostgresReportArtifactStore(dsn=dsn, table_prefix=table_prefix)
    assert store.migrate_legacy_reports() == 1
    assert store.migrate_legacy_reports() == 0
    artifacts = store.list_artifacts(session.session_id)
    assert len(artifacts) == 1
    assert artifacts[0].schema_version == "legacy-v1"
    assert store.get_head(session.session_id).active_report_id == artifacts[0].report_id
