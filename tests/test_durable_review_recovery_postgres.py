import os
from uuid import uuid4

import pytest

from app.graphs.durable_review_graph import DurableReviewGraphDependencies, build_durable_review_graph
from app.graphs.durable_review_state import make_durable_review_initial_state, review_thread_id
from app.services.langgraph_runtime import PostgresCheckpointerRuntime, VersionedGraphRegistry
from app.services.postgres_session import PostgresInterviewSessionStore
from app.services.prep import InterviewPlan, InterviewQuestion
from app.services.report_jobs import PostgresReportJobStore
from app.services.review_workflow import ReviewWorkflowService
from app.services.review_workflow_store import PostgresReviewWorkflowStore


pytestmark = pytest.mark.langgraph_review_recovery


def test_provider_retry_survives_saver_restart(monkeypatch):
    dsn = os.getenv("POSTGRES_DSN")
    if not dsn:
        pytest.skip("POSTGRES_DSN is required")
    monkeypatch.setenv("REPORT_LANGGRAPH_ROLLOUT_PERCENT", "100")
    prefix = "test_review_recovery_" + uuid4().hex[:12]
    session_store = PostgresInterviewSessionStore(dsn=dsn, table_prefix=prefix)
    jobs = PostgresReportJobStore(dsn=dsn, table_prefix=prefix)
    workflow_store = PostgresReviewWorkflowStore(dsn=dsn, table_prefix=prefix)
    turn = session_store.start(
        InterviewPlan(title="Backend", questions=[InterviewQuestion(id="q1", kind="project", prompt="Prompt", focus="focus")]),
        job_description="role", resume_text="resume", job_tags=["python"],
    )
    session_store.finish(turn.session_id)
    job = jobs.enqueue_report_request(turn.session_id)
    thread_id = review_thread_id(job["job_id"])
    attempts = []

    def make_service(runtime):
        def generate(state):
            attempts.append(state["provider_attempt"])
            if state["provider_attempt"] == 1:
                raise RuntimeError("provider down")
            return {"report_ref": "report:completed", "report_sha256": "digest"}
        deps = DurableReviewGraphDependencies(
            workflow_store=workflow_store,
            review_question=lambda state, question_id: None,
            generate_report=generate,
            validate_report=lambda state: "passed",
            commit_report=lambda state: None,
        )
        registry = VersionedGraphRegistry()
        registry.register("langgraph-review-v1", build_durable_review_graph(deps, checkpointer=runtime.start()))
        return ReviewWorkflowService(session_store=session_store, workflow_store=workflow_store, graph_registry=registry)

    first = PostgresCheckpointerRuntime(dsn)
    second = None
    try:
        service = make_service(first)
        service.run_claimed_job(job)
        graph = service.graph_for_job(job)
        config = {"configurable": {"thread_id": thread_id}}
        assert graph.get_state(config).next == ("wait_for_retry",)
        first.shutdown()

        second = PostgresCheckpointerRuntime(dsn)
        recovered = make_service(second)
        assert recovered.resume_retry(job, 2) == "completed"
        assert recovered.graph_for_job(job).get_state(config).next == ()
        assert attempts == [1, 2]
    finally:
        runtime = second or first
        try:
            runtime.delete_thread(thread_id)
        except Exception:
            pass
        runtime.shutdown()
        psycopg2, sql = PostgresReportJobStore._import_psycopg2()
        with psycopg2.connect(dsn) as connection:
            with connection.cursor() as cursor:
                for suffix in ("review_artifacts", "review_runs", "runtime_event_receipts", "runtime_outbox", "agent_runs", "report_jobs", "reports", "question_evaluations", "messages", "sessions"):
                    cursor.execute(sql.SQL("DROP TABLE IF EXISTS {table} CASCADE").format(table=sql.Identifier(f"{prefix}_{suffix}")))
