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
from tests.test_durable_review_graph import FakeStore
from tests.test_durable_review_state import make_finished_state, make_job
from tests.postgres_support import require_postgres_dsn


pytestmark = pytest.mark.langgraph_review_recovery


@pytest.mark.parametrize(
    "fault_point",
    [
        "after_review_run_initialize",
        "after_question_projection",
        "after_coach_generation",
        "after_quality_validation",
        "after_final_commit",
    ],
)
def test_graph_node_process_loss_replays_to_one_business_result(fault_point):
    dsn = require_postgres_dsn()
    runtime = PostgresCheckpointerRuntime(dsn)
    saver = runtime.start()
    thread_id = f"review:fault-{fault_point}-{uuid4().hex}"
    config = {"configurable": {"thread_id": thread_id}}
    store = FakeStore()
    reviewed = set()
    committed = set()
    raised = set()

    def inject(point, _state):
        if point == fault_point and point not in raised:
            raised.add(point)
            raise RuntimeError("injected process loss")

    def deps(fault_injector=None):
        return DurableReviewGraphDependencies(
            workflow_store=store,
            review_question=lambda state, question_id: reviewed.add(question_id),
            generate_report=lambda state: {"report_ref": "report:one", "report_sha256": "digest"},
            validate_report=lambda state: "passed",
            commit_report=lambda state: committed.add(state["report_sha256"]),
            fault_injector=fault_injector,
        )

    try:
        first = build_durable_review_graph(deps(inject), checkpointer=saver)
        with pytest.raises(RuntimeError, match="injected process loss"):
            first.invoke(make_durable_review_initial_state(make_job(), make_finished_state()), config)

        recovered = build_durable_review_graph(deps(), checkpointer=saver)
        result = recovered.invoke(None, config)

        assert result["report_sha256"] == "digest"
        assert reviewed == {"q1"}
        assert committed == {"digest"}
    finally:
        runtime.delete_thread(thread_id)
        runtime.shutdown()


def test_provider_retry_survives_saver_restart(monkeypatch):
    dsn = require_postgres_dsn()
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
