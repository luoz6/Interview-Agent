"""PostgreSQL integration coverage."""

from __future__ import annotations
from dataclasses import dataclass
from time import monotonic, sleep
from uuid import UUID, uuid4

import pytest
from langgraph.types import Command

from app.graphs.durable_interview_graph import (
    DurableInterviewGraphDependencies,
    build_durable_interview_graph,
)
from app.graphs.durable_review_graph import (
    DurableReviewGraphDependencies,
    build_durable_review_graph,
)
from app.graphs.durable_review_state import review_thread_id
from app.graphs.durable_interview_state import make_durable_initial_state
from app.services.interview_generation_store import (
    ChunkCoalescer,
    PostgresInterviewGenerationStore,
)
from app.services.followup_decision_service import (
    FollowupDecisionExecutionService,
)
from app.services.postgres_decision_store import PostgresDecisionStore
from app.services.interview_workflow import InterviewWorkflowService
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
from tests.integration.postgres.test_postgres_session_store import make_plan
from tests.report_worker_fixtures import make_report
from tests.langgraph_rollout_fixtures import (
    find_job_id_for_review_engine,
    find_session_id_for_interview_engine,
)


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


class StableExaminer:
    def stream_followup_attempt(self, *, context, execution_context):
        yield "deterministic follow-up"


class FailAfterReportEnqueue:
    def __init__(self, target) -> None:
        self.target = target
        self.triggered = False

    def enqueue_report_request(self, session_id):
        job = self.target.enqueue_report_request(session_id)
        if not self.triggered:
            self.triggered = True
            raise RuntimeError("lost after report enqueue")
        return job


def _claim_eventually(job_store, *, worker_id: str, timeout_seconds: float = 2):
    """Model the worker's bounded polling without weakening job identity."""
    deadline = monotonic() + timeout_seconds
    while True:
        claimed = job_store.claim_next(worker_id=worker_id)
        if claimed is not None or monotonic() >= deadline:
            return claimed
        sleep(0.02)


def _build_interview_service(
    *,
    session_store,
    workflow_store,
    generation_store,
    runtime,
    report_queue,
    rollout_percent,
):
    graph = build_durable_interview_graph(
        DurableInterviewGraphDependencies(
            workflow_store=workflow_store,
            generation_store=generation_store,
            decision_service=FollowupDecisionExecutionService(
                store=PostgresDecisionStore(
                    dsn=require_postgres_dsn(),
                    table_prefix=generation_store.table_prefix,
                ),
                provider=None,
            ),
            examiner=StableExaminer(),
            report_job_queue=report_queue,
            coalescer_factory=lambda: ChunkCoalescer(
                max_interval_seconds=0
            ),
        ),
        checkpointer=runtime.start(),
    )
    registry = VersionedGraphRegistry()
    registry.register("langgraph-v1", graph)
    service = InterviewWorkflowService(
        legacy_store=session_store,
        workflow_store=workflow_store,
        generation_store=generation_store,
        graph_registry=registry,
        runtime_store="postgres",
        runtime_enabled=True,
        rollout_percent=rollout_percent,
        default_graph_version="langgraph-v1",
    )
    return service, graph


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


def test_local_one_percent_joint_assignment_resumes_after_rollout_zero(
    monkeypatch,
):
    """Existing 1% ownership drains through fresh 0% runtime instances."""
    dsn = require_postgres_dsn()
    prefix = make_runtime_table_prefix("local_one_percent_drain")
    session_id = find_session_id_for_interview_engine("langgraph-v1", 1)
    job_id = find_job_id_for_review_engine("langgraph-review-v1", 1)
    interview_thread_id = session_id
    report_thread_id = review_thread_id(job_id)
    first_runtime = PostgresCheckpointerRuntime(dsn)
    resumed_runtime = None

    monkeypatch.setenv("INTERVIEW_RUNTIME_STORE", "postgres")
    monkeypatch.setenv("INTERVIEW_LANGGRAPH_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("REPORT_LANGGRAPH_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("INTERVIEW_LANGGRAPH_ROLLOUT_PERCENT", "1")
    monkeypatch.setenv("REPORT_LANGGRAPH_ROLLOUT_PERCENT", "1")

    try:
        session_store = PostgresInterviewSessionStore(
            dsn=dsn, table_prefix=prefix
        )
        workflow_store = PostgresInterviewWorkflowStore(
            dsn=dsn, table_prefix=prefix
        )
        generation_store = PostgresInterviewGenerationStore(
            dsn=dsn, table_prefix=prefix
        )
        jobs = PostgresReportJobStore(
            dsn=dsn, table_prefix=prefix, lease_seconds=30
        )
        faulting_queue = FailAfterReportEnqueue(jobs)

        with monkeypatch.context() as assignment:
            import app.services.interview_workflow as interview_module
            import app.services.report_jobs as report_jobs_module

            assignment.setattr(
                interview_module, "uuid4", lambda: UUID(session_id)
            )
            assignment.setattr(
                report_jobs_module, "uuid4", lambda: UUID(job_id)
            )
            first_service, first_graph = _build_interview_service(
                session_store=session_store,
                workflow_store=workflow_store,
                generation_store=generation_store,
                runtime=first_runtime,
                report_queue=faulting_queue,
                rollout_percent=1,
            )
            turn = first_service.start(
                make_plan(),
                job_description="synthetic role",
                resume_text="synthetic resume",
                job_tags=["python"],
            )
            assert turn.session_id == session_id

            first_service.submit_command(
                session_id,
                command_type="answer",
                expected_version=1,
                command_id="local-canary-answer",
                answer_text="deterministic answer",
            )
            first_graph.invoke(
                Command(
                    resume={
                        "kind": "answer_command",
                        "command_id": "local-canary-answer",
                    }
                ),
                config={"configurable": {"thread_id": session_id}},
            )
            assert session_store.get(session_id)["state_version"] == 3

            first_service.submit_command(
                session_id,
                command_type="finish",
                expected_version=3,
                command_id="local-canary-finish",
            )
            with pytest.raises(RuntimeError, match="after report enqueue"):
                first_graph.invoke(
                    Command(
                        resume={
                            "kind": "answer_command",
                            "command_id": "local-canary-finish",
                        }
                    ),
                    config={
                        "configurable": {"thread_id": session_id}
                    },
                )

        assigned_job = jobs.get_job(job_id)
        assert (
            session_store.get(session_id)["workflow_engine"]
            == "langgraph-v1"
        )
        assert assigned_job["review_engine"] == "langgraph-review-v1"
        assert jobs.count_jobs() == 1

        first_runtime.shutdown()
        monkeypatch.setenv("INTERVIEW_LANGGRAPH_ROLLOUT_PERCENT", "0")
        monkeypatch.setenv("REPORT_LANGGRAPH_ROLLOUT_PERCENT", "0")

        resumed_session_store = PostgresInterviewSessionStore(
            dsn=dsn, table_prefix=prefix
        )
        resumed_workflow_store = PostgresInterviewWorkflowStore(
            dsn=dsn, table_prefix=prefix
        )
        resumed_generation_store = PostgresInterviewGenerationStore(
            dsn=dsn, table_prefix=prefix
        )
        resumed_jobs = PostgresReportJobStore(
            dsn=dsn, table_prefix=prefix, lease_seconds=30
        )
        resumed_runtime = PostgresCheckpointerRuntime(dsn)
        resumed_interview, resumed_graph = _build_interview_service(
            session_store=resumed_session_store,
            workflow_store=resumed_workflow_store,
            generation_store=resumed_generation_store,
            runtime=resumed_runtime,
            report_queue=resumed_jobs,
            rollout_percent=0,
        )

        assert resumed_interview.graph_for_session(session_id) is resumed_graph
        resumed_graph.invoke(
            None,
            config={"configurable": {"thread_id": session_id}},
        )
        finished = resumed_session_store.get(session_id)
        messages = resumed_session_store.list_messages(session_id)
        assert finished["workflow_engine"] == "langgraph-v1"
        assert finished["status"] == "finished"
        assert sum(item["role"] == "candidate" for item in messages) == 1
        assert sum(item["role"] == "interviewer" for item in messages) == 2
        assert resumed_jobs.count_jobs() == 1
        assert (
            resumed_jobs.get_job(job_id)["review_engine"]
            == "langgraph-review-v1"
        )

        claimed = _claim_eventually(
            resumed_jobs,
            worker_id="local-canary-review-worker",
        )
        assert claimed is not None
        assert claimed["job_id"] == job_id
        review_store = PostgresReviewWorkflowStore(
            dsn=dsn, table_prefix=prefix
        )
        review_graph = build_durable_review_graph(
            DurableReviewGraphDependencies(
                workflow_store=review_store,
                review_question=lambda state, question_id: None,
                generate_report=lambda state: review_store.save_report_artifact(
                    job_id=state["job_id"],
                    report=make_report(state["session_id"]),
                ),
                validate_report=lambda state: "passed",
                commit_report=lambda state: review_store.commit_report(
                    job_id=state["job_id"],
                    report=review_store.load_report_artifact(
                        state["job_id"]
                    ),
                ),
            ),
            checkpointer=resumed_runtime.start(),
        )
        review_registry = VersionedGraphRegistry()
        review_registry.register("langgraph-review-v1", review_graph)
        review_service = ReviewWorkflowService(
            session_store=resumed_session_store,
            workflow_store=review_store,
            graph_registry=review_registry,
            checkpointer_runtime=resumed_runtime,
            job_store=resumed_jobs,
            lease_seconds=30,
        )

        result = review_service.run_claimed_job(
            claimed, worker_id="local-canary-review-worker"
        )

        assert result["report_sha256"]
        assert resumed_jobs.count_jobs() == 1
        assert resumed_jobs.count_reports() == 1
        assert resumed_jobs.get_job(job_id)["status"] == "completed"
        assert (
            resumed_session_store.get(session_id)["review_status"]
            == "completed"
        )
        psycopg2, sql = PostgresReportJobStore._import_psycopg2()
        with psycopg2.connect(dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        "SELECT COUNT(*) FROM {runs} WHERE job_id = %s::uuid"
                    ).format(runs=sql.Identifier(review_store.runs_table)),
                    (job_id,),
                )
                assert cursor.fetchone()[0] == 1
    finally:
        first_runtime.shutdown()
        if resumed_runtime is not None:
            resumed_runtime.shutdown()
        cleanup_runtime = PostgresCheckpointerRuntime(dsn)
        try:
            cleanup_runtime.start()
            cleanup_runtime.delete_thread(interview_thread_id)
            cleanup_runtime.delete_thread(report_thread_id)
        finally:
            cleanup_runtime.shutdown()
        _drop_prefix(dsn, prefix)
