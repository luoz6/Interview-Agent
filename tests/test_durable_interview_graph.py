from dataclasses import dataclass
from uuid import uuid4

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

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
from app.services.report_jobs import PostgresReportJobStore
from app.agents.examiner import fallback_followup
from app.services.report import ReportGenerationFailed
from tests.test_durable_interview_state import make_start_kwargs
from tests.test_postgres_session_store import require_dsn


@dataclass
class FakeCommand:
    command_id: str
    status: str
    expected_version: int = 1
    command_type: str = "answer"
    answer_text: str | None = "answer"


class FakeWorkflowStore:
    def __init__(self):
        self.commands = {}
        self.loaded_commands = []

    def seed_command(self, command_id, *, status):
        self.commands[command_id] = FakeCommand(command_id, status)

    def get_command(self, session_id, command_id):
        self.loaded_commands.append((session_id, command_id))
        return self.commands[command_id]

    def mark_command_conflict(self, session_id, command_id, state_version):
        self.commands[command_id].status = "conflict"


def make_graph():
    store = FakeWorkflowStore()

    def project_state(state):
        return {
            "state_version": state["state_version"] + 1,
            "command_outcome": None,
        }

    deps = DurableInterviewGraphDependencies(store, project_state)
    graph = build_durable_interview_graph(
        deps, checkpointer=InMemorySaver()
    )
    config = {"configurable": {"thread_id": "s1"}}
    return graph, config, deps


def make_initial_input():
    kwargs = make_start_kwargs()
    return make_durable_initial_state("s1", kwargs["plan"])


def test_graph_initializes_then_waits_for_answer():
    graph, config, _ = make_graph()

    result = graph.invoke(make_initial_input(), config=config)

    assert result["interview_status"] == "active"
    snapshot = graph.get_state(config)
    assert snapshot.next == ("wait_for_answer",)
    assert snapshot.tasks[0].interrupts


def test_answer_resume_stores_only_command_identity():
    graph, config, deps = make_graph()
    deps.workflow_store.seed_command("cmd-1", status="applied")
    graph.invoke(make_initial_input(), config=config)

    graph.invoke(
        Command(
            resume={"kind": "answer_command", "command_id": "cmd-1"}
        ),
        config=config,
    )

    assert deps.workflow_store.loaded_commands == [("s1", "cmd-1")]
    assert graph.get_state(config).next == ("wait_for_answer",)


class FakeExaminer:
    def __init__(self, *, fail=False):
        self.fail = fail
        self.attempt_count = 0

    def stream_followup_attempt(self, *, context, execution_context):
        self.attempt_count += 1
        if self.fail:
            raise ReportGenerationFailed("provider unavailable")
        yield "Generated follow-up."


def make_postgres_graph(*, fail=False):
    prefix = f"test_durable_graph_{uuid4().hex[:10]}"
    session_id = f"session-{uuid4().hex}"
    plan = make_start_kwargs()["plan"]
    session_store = PostgresInterviewSessionStore(
        dsn=require_dsn(), table_prefix=prefix
    )
    session_store.insert_durable_session_shell(
        session_id=session_id,
        plan=plan,
        job_description="Backend role",
        resume_text="Built APIs",
        job_tags=["python"],
    )
    workflow_store = PostgresInterviewWorkflowStore(
        dsn=require_dsn(), table_prefix=prefix
    )
    generation_store = PostgresInterviewGenerationStore(
        dsn=require_dsn(), table_prefix=prefix
    )
    examiner = FakeExaminer(fail=fail)
    report_jobs = PostgresReportJobStore(
        dsn=require_dsn(), table_prefix=prefix
    )
    workflow_store.report_jobs = report_jobs
    deps = DurableInterviewGraphDependencies(
        workflow_store=workflow_store,
        generation_store=generation_store,
        examiner=examiner,
        report_job_queue=report_jobs,
        retryable_provider_errors=(ReportGenerationFailed,),
    )
    graph = build_durable_interview_graph(
        deps, checkpointer=InMemorySaver()
    )
    config = {"configurable": {"thread_id": session_id}}
    graph.invoke(
        make_durable_initial_state(session_id, plan), config=config
    )
    return graph, config, workflow_store, examiner


@pytest.mark.pg_runtime
def test_successful_generation_commits_one_complete_message():
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
        Command(
            resume={"kind": "answer_command", "command_id": "cmd-1"}
        ),
        config=config,
    )

    state = graph.get_state(config).values
    assert state["messages"][-1]["content"] == "Generated follow-up."
    assert state["generation_id"] is None
    assert state["state_version"] == 3
    assert examiner.attempt_count == 1
    assert store.get_command(session_id, "cmd-1").status == "applied"


@pytest.mark.pg_runtime
def test_retry_interrupt_waits_for_due_event():
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
        Command(
            resume={"kind": "answer_command", "command_id": "cmd-1"}
        ),
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


@pytest.mark.pg_runtime
def test_third_failure_commits_template_fallback():
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
        Command(
            resume={"kind": "answer_command", "command_id": "cmd-1"}
        ),
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
    assert state["messages"][-1]["content"] == fallback_followup(
        "Architecture"
    )
    assert state["last_error_code"] == "provider_unavailable"
    assert state["state_version"] == 3
    assert examiner.attempt_count == 3


@pytest.mark.pg_runtime
def test_finish_projects_before_report_job_enqueue():
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
