from __future__ import annotations

import re
from uuid import uuid4

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
import psycopg2
from psycopg2 import sql
import pytest

from app.agents.examiner import fallback_followup
from app.graphs.durable_interview_graph import (
    DurableInterviewGraphDependencies,
    build_durable_interview_graph,
)
from app.graphs.durable_interview_state import make_durable_initial_state
from app.services.interview_generation_store import (
    PostgresInterviewGenerationStore,
)
from app.services.interview_workflow_store import (
    PostgresInterviewWorkflowStore,
)
from app.services.postgres_session import PostgresInterviewSessionStore
from app.services.report import ReportGenerationFailed
from app.services.report_jobs import PostgresReportJobStore
from tests.integration.postgres.test_postgres_session_store import require_dsn
from tests.postgres_support import make_runtime_table_prefix
from tests.unit.test_durable_interview_state import make_start_kwargs


pytestmark = pytest.mark.pg_runtime

DURABLE_GRAPH_TEST_TABLE = re.compile(
    r"^test_durable_graph_[0-9a-f]{10}_[a-z0-9_]+$"
)


def _durable_graph_test_tables() -> set[str]:
    with psycopg2.connect(require_dsn()) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT tablename
                FROM pg_tables
                WHERE schemaname = 'public'
                  AND tablename LIKE 'test_durable_graph_%'
                """
            )
            return {row[0] for row in cursor.fetchall()}


@pytest.fixture
def durable_graph_table_cleanup():
    before = _durable_graph_test_tables()
    yield
    created = _durable_graph_test_tables() - before
    if not created:
        return
    if any(DURABLE_GRAPH_TEST_TABLE.fullmatch(name) is None for name in created):
        pytest.fail("refusing to clean a non-isolated durable graph relation")
    with psycopg2.connect(require_dsn()) as connection:
        with connection.cursor() as cursor:
            for name in sorted(created):
                cursor.execute(
                    sql.SQL("DROP TABLE IF EXISTS {} CASCADE").format(
                        sql.Identifier(name)
                    )
                )
    assert _durable_graph_test_tables() == before


class FakeExaminer:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.attempt_count = 0

    def stream_followup_attempt(self, *, context, execution_context):
        self.attempt_count += 1
        if self.fail:
            raise ReportGenerationFailed("provider unavailable")
        yield "Generated follow-up."


def make_postgres_graph(*, fail: bool = False):
    prefix = make_runtime_table_prefix("durable_graph")
    session_id = f"session-{uuid4().hex}"
    plan = make_start_kwargs()["plan"]
    session_store = PostgresInterviewSessionStore(
        dsn=require_dsn(),
        table_prefix=prefix,
    )
    session_store.insert_durable_session_shell(
        session_id=session_id,
        plan=plan,
        job_description="Backend role",
        resume_text="Built APIs",
        job_tags=["python"],
    )
    workflow_store = PostgresInterviewWorkflowStore(
        dsn=require_dsn(),
        table_prefix=prefix,
    )
    generation_store = PostgresInterviewGenerationStore(
        dsn=require_dsn(),
        table_prefix=prefix,
    )
    examiner = FakeExaminer(fail=fail)
    report_jobs = PostgresReportJobStore(
        dsn=require_dsn(),
        table_prefix=prefix,
    )
    workflow_store.report_jobs = report_jobs
    deps = DurableInterviewGraphDependencies(
        workflow_store=workflow_store,
        generation_store=generation_store,
        examiner=examiner,
        report_job_queue=report_jobs,
    )
    graph = build_durable_interview_graph(
        deps,
        checkpointer=InMemorySaver(),
    )
    config = {"configurable": {"thread_id": session_id}}
    graph.invoke(make_durable_initial_state(session_id, plan), config=config)
    return graph, config, workflow_store, examiner


def test_successful_generation_commits_one_complete_message(
    durable_graph_table_cleanup,
):
    graph, config, store, examiner = make_postgres_graph()
    session_id = config["configurable"]["thread_id"]
    store.enqueue_command(
        session_id=session_id,
        command_id="cmd-1",
        command_type="answer",
        expected_version=1,
        answer_text="I used cache-aside.",
    )

    graph.invoke(
        Command(resume={"kind": "answer_command", "command_id": "cmd-1"}),
        config=config,
    )

    state = graph.get_state(config).values
    assert state["messages"][-1]["content"] == "Generated follow-up."
    assert state["generation_id"] is None
    assert state["state_version"] == 3
    assert examiner.attempt_count == 1
    assert store.get_command(session_id, "cmd-1").status == "applied"


def test_retry_interrupt_waits_for_due_event(durable_graph_table_cleanup):
    graph, config, store, examiner = make_postgres_graph(fail=True)
    session_id = config["configurable"]["thread_id"]
    store.enqueue_command(
        session_id=session_id,
        command_id="cmd-1",
        command_type="answer",
        expected_version=1,
        answer_text="I used cache-aside.",
    )

    graph.invoke(
        Command(resume={"kind": "answer_command", "command_id": "cmd-1"}),
        config=config,
    )

    snapshot = graph.get_state(config)
    event = store.control.list_outbox(
        session_id=session_id,
        status="pending",
    )[-1]
    assert snapshot.next == ("wait_for_retry",)
    assert event["event_type"] == "interview_retry_due"
    assert event["available_at"] > event["created_at"]
    assert examiner.attempt_count == 1

    graph.invoke(
        Command(
            resume={
                "kind": "retry_timer",
                "generation_id": snapshot.values["generation_id"],
                "next_attempt_number": 3,
            }
        ),
        config=config,
    )
    stale = graph.get_state(config)
    assert stale.next == ("wait_for_retry",)
    assert stale.values["generation_attempt"] == 1


def test_third_failure_commits_template_fallback(durable_graph_table_cleanup):
    graph, config, store, examiner = make_postgres_graph(fail=True)
    session_id = config["configurable"]["thread_id"]
    store.enqueue_command(
        session_id=session_id,
        command_id="cmd-1",
        command_type="answer",
        expected_version=1,
        answer_text="I used cache-aside.",
    )
    graph.invoke(
        Command(resume={"kind": "answer_command", "command_id": "cmd-1"}),
        config=config,
    )
    generation_id = graph.get_state(config).values["generation_id"]
    for attempt in (2, 3):
        graph.invoke(
            Command(
                resume={
                    "kind": "retry_timer",
                    "generation_id": generation_id,
                    "next_attempt_number": attempt,
                }
            ),
            config=config,
        )

    state = graph.get_state(config).values
    assert state["messages"][-1]["content"] == fallback_followup("Architecture")
    assert state["last_error_code"] == "provider_unavailable"
    assert state["state_version"] == 3
    assert examiner.attempt_count == 3


def test_finish_projects_before_report_job_enqueue(
    durable_graph_table_cleanup,
):
    graph, config, store, _ = make_postgres_graph()
    session_id = config["configurable"]["thread_id"]
    store.enqueue_command(
        session_id=session_id,
        command_id="cmd-finish",
        command_type="finish",
        expected_version=1,
    )

    graph.invoke(
        Command(
            resume={
                "kind": "answer_command",
                "command_id": "cmd-finish",
            }
        ),
        config=config,
    )

    assert store.session_snapshot(session_id)["status"] == "finished"
    assert store.session_snapshot(session_id)["state_version"] == 2
    assert store.report_jobs.get_job_by_session(session_id) is not None
