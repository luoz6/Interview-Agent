"""PostgreSQL integration tests for agent run recorders."""

import pytest

from app.services.agent_recorders import PostgresAgentRunRecorder
from app.services.postgres_runtime_control import PostgresRuntimeControlStore
from app.services.postgres_session import PostgresInterviewSessionStore
from app.services.prep import InterviewPlan, InterviewQuestion
from tests.agent_runtime_fixtures import make_record
from tests.postgres_support import make_runtime_table_prefix, require_postgres_dsn


@pytest.fixture
def pg_control():
    dsn = require_postgres_dsn()
    prefix = make_runtime_table_prefix("agent_runs")
    session_store = PostgresInterviewSessionStore(
        dsn=dsn,
        table_prefix=prefix,
    )
    control = PostgresRuntimeControlStore(
        dsn=dsn,
        table_prefix=prefix,
    )
    turn = session_store.start(
        InterviewPlan(
            title="Agent run ledger",
            questions=[
                InterviewQuestion(
                    id="q1",
                    kind="technical",
                    prompt="Explain tracing.",
                    focus="runtime tracing",
                )
            ],
        ),
        job_description="Backend observability role",
        resume_text="Built trace pipelines",
        job_tags=["python"],
    )
    yield control, turn.session_id


@pytest.mark.pg_control
def test_postgres_insert_is_idempotent(pg_control):
    control, session_id = pg_control
    record = make_record(session_id)
    recorder = PostgresAgentRunRecorder(control)

    recorder.record(record)
    recorder.record(record)

    assert control.count_agent_runs(record.run_id) == 1


@pytest.mark.pg_control
def test_public_query_excludes_safe_metadata(pg_control):
    control, session_id = pg_control
    control.record_agent_run(make_record(session_id))

    item = control.list_agent_runs(session_id=session_id)[0]

    assert "safe_metadata" not in item
    assert item["attempt_number"] == 2


@pytest.mark.pg_control
def test_child_run_persists_parent_run_id(pg_control):
    control, session_id = pg_control
    control.record_agent_run(
        make_record(session_id, parent_run_id="agent-parent")
    )

    assert control.list_agent_runs(session_id=session_id)[0][
        "parent_run_id"
    ] == "agent-parent"
