from threading import Event

import pytest

from app.graphs.durable_interview_graph import GenerationLeaseHeartbeat
from app.services.interview_generation_store import (
    PostgresInterviewGenerationStore,
)
from app.services.postgres_session import PostgresInterviewSessionStore
from app.services.report_jobs import PostgresReportJobStore
from app.services.review_execution import bind_review_execution_lease
from app.services.review_workflow import ReportLeaseHeartbeat
from app.services.review_workflow_store import (
    PostgresReviewWorkflowStore,
    ReviewEffectHeartbeat,
)
from app.services.workflow_thread_lock import (
    FencedWriteRejected,
    GenerationLeaseLost,
    ReportLeaseLost,
    ReviewEffectLeaseLost,
)
from tests.postgres_support import make_runtime_table_prefix
from tests.test_postgres_session_store import make_plan
from tests.test_runtime_signal_metrics_postgres import _drop_prefix


pytestmark = pytest.mark.langgraph_heartbeat_recovery


@pytest.fixture
def isolated_runtime(postgres_dsn):
    prefix = make_runtime_table_prefix("hbr")
    sessions = PostgresInterviewSessionStore(
        dsn=postgres_dsn, table_prefix=prefix
    )
    jobs = PostgresReportJobStore(
        dsn=postgres_dsn, table_prefix=prefix, lease_seconds=30
    )
    generations = PostgresInterviewGenerationStore(
        dsn=postgres_dsn, table_prefix=prefix
    )
    effects = PostgresReviewWorkflowStore(
        dsn=postgres_dsn, table_prefix=prefix, effect_lease_seconds=30
    )
    try:
        yield prefix, sessions, jobs, generations, effects
    finally:
        _drop_prefix(postgres_dsn, prefix)


class RaisingGenerationRenewal:
    def __init__(self, failure):
        self.failure = failure
        self.called = Event()

    def heartbeat_attempt(self, *args, **kwargs):
        self.called.set()
        raise self.failure


class RaisingReportRenewal:
    def __init__(self, jobs, failure):
        self.jobs = jobs
        self.failure = failure
        self.called = Event()

    def assert_lease(self, *args, **kwargs):
        return self.jobs.assert_lease(*args, **kwargs)

    def heartbeat(self, *args, **kwargs):
        self.called.set()
        raise self.failure


class RaisingEffectRenewal:
    def __init__(self, failure):
        self.failure = failure
        self.called = Event()

    def heartbeat_effect(self, *args, **kwargs):
        self.called.set()
        raise self.failure


def _expire_generation_attempt(store, generation_id, attempt_number):
    with store._connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                store._sql(
                    """
                    UPDATE {attempts}
                    SET lease_expires_at = NOW() - INTERVAL '1 second'
                    WHERE generation_id = %s AND attempt_number = %s
                    """
                ),
                (generation_id, attempt_number),
            )


def _expire_report_job(jobs, job_id):
    psycopg2, sql = jobs._import_psycopg2()
    with psycopg2.connect(jobs.dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                sql.SQL(
                    """
                    UPDATE {jobs}
                    SET lease_expires_at = NOW() - INTERVAL '1 second'
                    WHERE job_id = %s::uuid
                    """
                ).format(jobs=sql.Identifier(jobs.jobs_table)),
                (job_id,),
            )


def _expire_effect_claim(workflow, operation_key):
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


def test_generation_renewal_exception_fences_old_owner_and_replacement_wins(
    isolated_runtime,
):
    _, sessions, _, generations, _ = isolated_runtime
    session = sessions.start(
        make_plan(),
        job_description="role",
        resume_text="resume",
        job_tags=["python"],
    )
    generation = generations.prepare_generation(
        session_id=session.session_id,
        source_command_id="command-1",
        question_id="q1",
    )
    first = generations.start_or_reclaim_attempt(
        generation.generation_id,
        generation.active_attempt,
        worker_id="worker-a",
        lease_seconds=30,
    )
    failure = RuntimeError("renewal unavailable")
    renewal = RaisingGenerationRenewal(failure)
    heartbeat = GenerationLeaseHeartbeat(
        generation_store=renewal,
        attempt=first,
        worker_id="worker-a",
        lease_seconds=30,
    )
    heartbeat.interval_seconds = 0.01

    with heartbeat:
        assert renewal.called.wait(timeout=1)
        assert heartbeat._thread is not None
        heartbeat._thread.join(timeout=1)
        with pytest.raises(GenerationLeaseLost) as caught:
            heartbeat.ensure_owned()
    assert caught.value.__cause__ is failure

    _expire_generation_attempt(
        generations, first.generation_id, first.attempt_number
    )
    replacement = generations.start_or_reclaim_attempt(
        first.generation_id,
        first.attempt_number,
        worker_id="worker-b",
        lease_seconds=30,
    )
    assert replacement.attempt_number > first.attempt_number
    assert replacement.fencing_version >= first.fencing_version

    with pytest.raises(GenerationLeaseLost):
        generations.append_chunk(
            first.generation_id,
            first.attempt_number,
            1,
            "stale",
            lease_token=first.lease_token,
            fencing_version=first.fencing_version,
        )
    generations.complete_attempt(
        replacement.generation_id,
        replacement.attempt_number,
        "winner",
        lease_token=replacement.lease_token,
        fencing_version=replacement.fencing_version,
    )
    assert generations.get_by_id(first.generation_id).final_text == "winner"


def test_report_renewal_exception_allows_only_replacement_job_owner(
    isolated_runtime, monkeypatch
):
    _, sessions, jobs, _, _ = isolated_runtime
    monkeypatch.setenv("REPORT_LANGGRAPH_ROLLOUT_PERCENT", "100")
    session = sessions.start(
        make_plan(),
        job_description="role",
        resume_text="resume",
        job_tags=["python"],
    )
    sessions.finish(session.session_id)
    jobs.enqueue_report_request(session.session_id)
    first = jobs.claim_next(worker_id="worker-a", lease_seconds=30)
    failure = RuntimeError("renewal unavailable")
    renewal = RaisingReportRenewal(jobs, failure)
    heartbeat = ReportLeaseHeartbeat(
        job_store=renewal,
        job_id=first["job_id"],
        worker_id="worker-a",
        lease_token=first["lease_token"],
        lease_seconds=30,
    )
    heartbeat.interval_seconds = 0.01

    with heartbeat:
        assert renewal.called.wait(timeout=1)
        assert heartbeat._thread is not None
        heartbeat._thread.join(timeout=1)
        with pytest.raises(ReportLeaseLost) as caught:
            heartbeat.ensure_owned()
    assert caught.value.__cause__ is failure

    _expire_report_job(jobs, first["job_id"])
    replacement = jobs.claim_next(worker_id="worker-b", lease_seconds=30)
    assert replacement["job_id"] == first["job_id"]
    assert replacement["lease_token"] != first["lease_token"]
    assert not jobs.assert_lease(
        first["job_id"],
        worker_id="worker-a",
        lease_token=first["lease_token"],
    )
    assert jobs.assert_lease(
        replacement["job_id"],
        worker_id="worker-b",
        lease_token=replacement["lease_token"],
    )


def test_effect_renewal_exception_fences_complete_and_failure_mutations(
    isolated_runtime, monkeypatch
):
    _, sessions, jobs, _, workflow = isolated_runtime
    monkeypatch.setenv("REPORT_LANGGRAPH_ROLLOUT_PERCENT", "100")
    session = sessions.start(
        make_plan(),
        job_description="role",
        resume_text="resume",
        job_tags=["python"],
    )
    sessions.finish(session.session_id)
    jobs.enqueue_report_request(session.session_id)
    first_job = jobs.claim_next(worker_id="worker-a", lease_seconds=30)
    operation_key = f"question:{first_job['job_id']}:q1"
    with bind_review_execution_lease(
        job_id=first_job["job_id"],
        worker_id="worker-a",
        lease_token=first_job["lease_token"],
    ):
        first = workflow.claim_effect(
            operation_key=operation_key,
            job_id=first_job["job_id"],
            effect_type="question_review",
            question_id="q1",
            graph_schema_version="langgraph-review-v1",
            input_sha256="input-q1",
        )

    failure = RuntimeError("renewal unavailable")
    renewal = RaisingEffectRenewal(failure)
    heartbeat = ReviewEffectHeartbeat(renewal, first, lease_seconds=30)
    heartbeat.interval_seconds = 0.01
    with heartbeat:
        assert renewal.called.wait(timeout=1)
        assert heartbeat._thread is not None
        heartbeat._thread.join(timeout=1)
        with pytest.raises(ReviewEffectLeaseLost) as caught:
            heartbeat.ensure_owned()
    assert caught.value.__cause__ is failure

    _expire_effect_claim(workflow, operation_key)
    _expire_report_job(jobs, first_job["job_id"])
    replacement_job = jobs.claim_next(
        worker_id="worker-b", lease_seconds=30
    )
    with bind_review_execution_lease(
        job_id=replacement_job["job_id"],
        worker_id="worker-b",
        lease_token=replacement_job["lease_token"],
    ):
        replacement = workflow.claim_effect(
            operation_key=operation_key,
            job_id=replacement_job["job_id"],
            effect_type="question_review",
            question_id="q1",
            graph_schema_version="langgraph-review-v1",
            input_sha256="input-q1",
        )
        assert replacement.fencing_version > first.fencing_version
        with pytest.raises(FencedWriteRejected):
            workflow.complete_effect(first, {"value": "stale"})
        with pytest.raises(ReviewEffectLeaseLost):
            workflow.fail_effect(first)
        winner = workflow.complete_effect(
            replacement, {"value": "winner"}
        )

    assert winner["payload"] == {"value": "winner"}
    assert workflow.load_effect_payload(operation_key) == {"value": "winner"}
