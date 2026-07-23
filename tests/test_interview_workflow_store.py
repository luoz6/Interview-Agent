from uuid import uuid4

import pytest

from app.services.interview_workflow_store import (
    CommandPayloadConflict,
    PostgresInterviewWorkflowStore,
)
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
