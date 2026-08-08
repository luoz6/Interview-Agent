from copy import deepcopy
from datetime import timedelta
from uuid import uuid4

import pytest

from app.services.interview_workflow_store import (
    BootstrapConflict,
    CommandPayloadConflict,
    PostgresInterviewWorkflowStore,
    ProjectionConflict,
)
from app.graphs.durable_interview_state import make_durable_initial_state
from app.services.postgres_session import PostgresInterviewSessionStore
from tests.test_postgres_session_store import make_plan, require_dsn


@pytest.fixture
def workflow_store():
    prefix = f"test_workflow_{uuid4().hex[:12]}"
    session_store = PostgresInterviewSessionStore(
        dsn=require_dsn(), table_prefix=prefix
    )
    turn = session_store.start(
        make_plan(),
        job_description="Backend role",
        resume_text="Built APIs",
        job_tags=["python"],
    )
    store = PostgresInterviewWorkflowStore(
        dsn=require_dsn(), table_prefix=prefix
    )
    store.session_id = turn.session_id
    return store


@pytest.fixture
def durable_workflow_store():
    prefix = f"test_projection_{uuid4().hex[:12]}"
    session_store = PostgresInterviewSessionStore(
        dsn=require_dsn(), table_prefix=prefix
    )
    session_id = f"session-{uuid4().hex}"
    plan = make_plan()
    session_store.insert_durable_session_shell(
        session_id=session_id,
        plan=plan,
        job_description="Backend role",
        resume_text="Built APIs",
        job_tags=["python"],
    )
    store = PostgresInterviewWorkflowStore(
        dsn=require_dsn(), table_prefix=prefix
    )
    store.session_id = session_id
    store.plan = plan
    return store


def test_command_event_contains_no_answer():
    from app.services.runtime_domain_events import InterviewCommandReadyEvent

    payload = InterviewCommandReadyEvent(
        event_id="command-event-1", session_id="s1", command_id="cmd-1"
    ).model_dump(mode="json")

    assert payload["event_type"] == "interview_command_ready"
    assert payload["command_id"] == "cmd-1"
    assert "answer" not in str(payload).lower()


def test_enqueue_command_commits_inbox_and_outbox_atomically(workflow_store):
    command = workflow_store.enqueue_command(
        session_id=workflow_store.session_id,
        command_id="cmd-1",
        command_type="answer",
        expected_version=1,
        answer_text="I used cache-aside.",
    )

    assert command.status == "pending"
    assert workflow_store.get_command(
        workflow_store.session_id, "cmd-1"
    ).answer_text == "I used cache-aside."
    event = workflow_store.control.list_outbox(
        session_id=workflow_store.session_id
    )[0]
    assert event["event_type"] == "interview_command_ready"
    assert "cache-aside" not in str(event["payload"]).lower()


def test_duplicate_command_with_changed_payload_is_rejected(workflow_store):
    workflow_store.enqueue_command(
        session_id=workflow_store.session_id,
        command_id="cmd-1",
        command_type="answer",
        expected_version=1,
        answer_text="first",
    )

    with pytest.raises(CommandPayloadConflict):
        workflow_store.enqueue_command(
            session_id=workflow_store.session_id,
            command_id="cmd-1",
            command_type="answer",
            expected_version=1,
            answer_text="changed",
        )


def test_missing_command_lookup_returns_none(workflow_store):
    assert (
        workflow_store.get_command_or_none(
            workflow_store.session_id, "missing-command"
        )
        is None
    )


def test_applied_command_payload_can_be_cleared(workflow_store):
    command = workflow_store.enqueue_command(
        session_id=workflow_store.session_id,
        command_id="cmd-private",
        command_type="answer",
        expected_version=1,
        answer_text="private answer",
    )
    workflow_store.mark_command_applied(
        workflow_store.session_id, command.command_id, 2
    )

    with workflow_store.control.connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT clock_timestamp()")
            database_now = cursor.fetchone()[0]

    assert workflow_store.clear_applied_command_payloads(
        older_than=database_now + timedelta(seconds=1)
    ) == 1
    cleared = workflow_store.get_command(
        workflow_store.session_id, command.command_id
    )
    assert cleared.answer_text is None
    assert cleared.payload_sha256 == command.payload_sha256


def test_projection_advances_one_public_version(durable_workflow_store):
    state = make_durable_initial_state(
        durable_workflow_store.session_id,
        durable_workflow_store.plan,
    )

    result = durable_workflow_store.project_state(state)

    assert result.state_version == 1
    assert durable_workflow_store.session_snapshot(
        durable_workflow_store.session_id
    )["state_version"] == 1


def test_projection_replay_reuses_same_version(durable_workflow_store):
    state = make_durable_initial_state(
        durable_workflow_store.session_id,
        durable_workflow_store.plan,
    )

    first = durable_workflow_store.project_state(state)
    second = durable_workflow_store.project_state(state)

    assert first == second
    assert second.state_version == 1
    assert durable_workflow_store.count_messages(
        durable_workflow_store.session_id
    ) == len(state["messages"])


def test_projection_rejects_same_version_with_changed_payload(
    durable_workflow_store,
):
    state = make_durable_initial_state(
        durable_workflow_store.session_id,
        durable_workflow_store.plan,
    )
    durable_workflow_store.project_state(state)
    changed = deepcopy(state)
    changed["messages"][-1]["content"] = "changed"

    with pytest.raises(ProjectionConflict):
        durable_workflow_store.project_state(changed)


def test_projection_rejects_replay_that_is_more_than_one_version_behind(
    durable_workflow_store,
):
    original = make_durable_initial_state(
        durable_workflow_store.session_id,
        durable_workflow_store.plan,
    )
    durable_workflow_store.project_state(original)
    next_state = deepcopy(original)
    next_state["state_version"] = 1
    durable_workflow_store.project_state(next_state)

    with pytest.raises(ProjectionConflict):
        durable_workflow_store.project_state(original)


def test_bootstrap_digest_is_write_once_before_public_projection(
    durable_workflow_store,
):
    store = durable_workflow_store
    store.register_bootstrap_input(
        session_id=store.session_id,
        graph_schema_version="langgraph-v1",
        bootstrap_input_sha256="a" * 64,
    )
    store.register_bootstrap_input(
        session_id=store.session_id,
        graph_schema_version="langgraph-v1",
        bootstrap_input_sha256="a" * 64,
    )

    with pytest.raises(BootstrapConflict):
        store.register_bootstrap_input(
            session_id=store.session_id,
            graph_schema_version="langgraph-v1",
            bootstrap_input_sha256="b" * 64,
        )

    with store.control.connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                store._sql(
                    """
                    SELECT bootstrap_input_sha256 FROM {sessions}
                    WHERE session_id = %s
                    """
                ),
                (store.session_id,),
            )
            assert cursor.fetchone() == ("a" * 64,)
