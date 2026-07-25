from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

import pytest

from app.graphs.durable_review_graph import (
    DurableReviewGraphDependencies,
    build_durable_review_graph,
)
from app.graphs.durable_review_state import review_thread_id
from app.graphs.durable_interview_state import make_durable_initial_state
from app.services.interview_workflow_store import (
    PostgresInterviewWorkflowStore,
)
from app.services.langgraph_runtime import (
    PostgresCheckpointerRuntime,
    VersionedGraphRegistry,
)
from app.services.postgres_session import PostgresInterviewSessionStore
from app.services.report_jobs import PostgresReportJobStore
from app.services.review_workflow import ReviewWorkflowService
from app.services.review_workflow_store import PostgresReviewWorkflowStore
from tests.postgres_support import (
    assert_safe_test_prefix,
    make_runtime_table_prefix,
    require_postgres_dsn,
)
from tests.test_postgres_session_store import make_plan
from tests.test_report_worker import make_report


pytestmark = pytest.mark.langgraph_dual_canary


def _drop_prefix(dsn: str, prefix: str) -> None:
    assert_safe_test_prefix(prefix)
    psycopg2, sql = PostgresReportJobStore._import_psycopg2()
    suffixes = (
        "review_artifacts",
        "review_runs",
        "generation_chunks",
        "generation_attempts",
        "generations",
        "workflow_commands",
        "runtime_event_receipts",
        "runtime_outbox",
        "agent_runs",
        "report_jobs",
        "reports",
        "question_evaluations",
        "messages",
        "sessions",
    )
    with psycopg2.connect(dsn) as connection:
        with connection.cursor() as cursor:
            for suffix in suffixes:
                cursor.execute(
                    sql.SQL("DROP TABLE IF EXISTS {table} CASCADE").format(
                        table=sql.Identifier(f"{prefix}_{suffix}")
                    )
                )


@pytest.mark.parametrize(
    ("interview_engine", "review_engine"),
    [
        ("legacy", "legacy"),
        ("langgraph-v1", "legacy"),
        ("legacy", "langgraph-review-v1"),
        ("langgraph-v1", "langgraph-review-v1"),
    ],
)
def test_four_engine_combinations_persist_independently(
    monkeypatch, interview_engine, review_engine
):
    dsn = require_postgres_dsn()
    prefix = make_runtime_table_prefix("dual_matrix")
    store = PostgresInterviewSessionStore(dsn=dsn, table_prefix=prefix)
    jobs = PostgresReportJobStore(dsn=dsn, table_prefix=prefix)
    plan = make_plan()
    try:
        if interview_engine == "langgraph-v1":
            session_id = f"durable-{uuid4().hex}"
            store.insert_durable_session_shell(
                session_id=session_id,
                plan=plan,
                job_description="role",
                resume_text="resume",
                job_tags=["python"],
            )
            PostgresInterviewWorkflowStore(
                dsn=dsn, table_prefix=prefix
            ).project_state(make_durable_initial_state(session_id, plan))
        else:
            session_id = store.start(
                plan,
                job_description="role",
                resume_text="resume",
                job_tags=["python"],
            ).session_id
        store.finish(session_id)
        monkeypatch.setenv(
            "REPORT_LANGGRAPH_ROLLOUT_PERCENT",
            "100" if review_engine == "langgraph-review-v1" else "0",
        )

        job = jobs.enqueue_report_request(session_id)

        assert store.get(session_id)["workflow_engine"] == interview_engine
        assert job["review_engine"] == review_engine
        assert jobs.enqueue_report_request(session_id)["job_id"] == job["job_id"]
        assert jobs.get_job(job["job_id"])["review_engine"] == review_engine
    finally:
        _drop_prefix(dsn, prefix)


@dataclass
class FailBeforeFirstCheckpoint:
    graph: object

    def get_state(self, config):
        return self.graph.get_state(config)

    def invoke(self, state, *, config):
        raise RuntimeError("lost before first checkpoint")


def _expire_lease(dsn: str, table: str, job_id: str) -> None:
    psycopg2, sql = PostgresReportJobStore._import_psycopg2()
    with psycopg2.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                sql.SQL(
                    """
                    UPDATE {jobs}
                    SET lease_expires_at = NOW() - INTERVAL '1 second'
                    WHERE job_id = %s::uuid
                    """
                ).format(jobs=sql.Identifier(table)),
                (job_id,),
            )


def test_review_cold_start_before_first_checkpoint_recovers_once(monkeypatch):
    dsn = require_postgres_dsn()
    prefix = make_runtime_table_prefix("review_cold_start")
    monkeypatch.setenv("REPORT_LANGGRAPH_ROLLOUT_PERCENT", "100")
    session_store = PostgresInterviewSessionStore(dsn=dsn, table_prefix=prefix)
    jobs = PostgresReportJobStore(dsn=dsn, table_prefix=prefix, lease_seconds=30)
    workflow_store = PostgresReviewWorkflowStore(dsn=dsn, table_prefix=prefix)
    runtime = PostgresCheckpointerRuntime(dsn)
    thread_id = None
    try:
        turn = session_store.start(
            make_plan(),
            job_description="role",
            resume_text="resume",
            job_tags=["python"],
        )
        session_store.finish(turn.session_id)
        job = jobs.enqueue_report_request(turn.session_id)
        first_claim = jobs.claim_next(worker_id="worker-first")
        thread_id = review_thread_id(job["job_id"])
        saver = runtime.start()
        graph = build_durable_review_graph(
            DurableReviewGraphDependencies(
                workflow_store=workflow_store,
                review_question=lambda state, question_id: None,
                generate_report=lambda state: workflow_store.save_report_artifact(
                    job_id=state["job_id"],
                    report=make_report(state["session_id"]),
                ),
                validate_report=lambda state: "passed",
                commit_report=lambda state: workflow_store.commit_report(
                    job_id=state["job_id"],
                    report=workflow_store.load_report_artifact(
                        state["job_id"]
                    ),
                ),
            ),
            checkpointer=saver,
        )
        failed_registry = VersionedGraphRegistry()
        failed_registry.register(
            "langgraph-review-v1", FailBeforeFirstCheckpoint(graph)
        )
        failed_service = ReviewWorkflowService(
            session_store=session_store,
            workflow_store=workflow_store,
            graph_registry=failed_registry,
            job_store=jobs,
            lease_seconds=30,
        )

        with pytest.raises(RuntimeError, match="before first checkpoint"):
            failed_service.run_claimed_job(
                first_claim, worker_id="worker-first"
            )
        assert not graph.get_state(
            {"configurable": {"thread_id": thread_id}}
        ).values

        _expire_lease(dsn, jobs.jobs_table, job["job_id"])
        second_claim = jobs.claim_next(worker_id="worker-second")
        registry = VersionedGraphRegistry()
        registry.register("langgraph-review-v1", graph)
        recovered = ReviewWorkflowService(
            session_store=session_store,
            workflow_store=workflow_store,
            graph_registry=registry,
            job_store=jobs,
            lease_seconds=30,
        )

        result = recovered.run_claimed_job(
            second_claim, worker_id="worker-second"
        )

        assert result["report_sha256"]
        assert jobs.count_jobs() == 1
        assert jobs.count_reports() == 1
        assert jobs.get_job(job["job_id"])["status"] == "completed"
        psycopg2, sql = PostgresReportJobStore._import_psycopg2()
        with psycopg2.connect(dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        "SELECT COUNT(*) FROM {runs} WHERE job_id = %s::uuid"
                    ).format(runs=sql.Identifier(workflow_store.runs_table)),
                    (job["job_id"],),
                )
                assert cursor.fetchone()[0] == 1
    finally:
        if thread_id is not None:
            runtime.delete_thread(thread_id)
        runtime.shutdown()
        _drop_prefix(dsn, prefix)
