from __future__ import annotations

from app.application.interview.session_commands import (
    DurableSessionStream,
    InterviewApplicationService,
    LegacySessionStream,
    StreamingTurnService,
)
from app.domain.interview.commands import SessionCommand
from app.domain.interview.errors import SessionDeletingError
from app.services.prep import InterviewPlan, InterviewQuestion
from app.services.runtime_events import AcceptedInterviewCommand
from app.services.session import InterviewSessionStore


class StubLLM:
    def generate_followup(self, context):
        return "Please add the trade-off."

    def stream_followup(self, context):
        yield "Please add "
        yield "the trade-off."


class RecordingPublisher:
    def __init__(self):
        self.events = []

    def publish(self, event):
        self.events.append(event)


class RecordingJobs:
    def __init__(self):
        self.sessions = []

    def enqueue_report_request(self, session_id):
        self.sessions.append(session_id)
        return {"job_id": f"job-{session_id}", "session_id": session_id}


class WorkflowSpy:
    def __init__(self):
        self.commands = []
        self.snapshots = []
        self.event_stream = self

    def submit_command(self, session_id, **kwargs):
        self.commands.append((session_id, kwargs))
        return AcceptedInterviewCommand(
            session_id=session_id,
            command_id=kwargs["command_id"] or "generated-command",
            workflow_engine="langgraph-v2",
            stream_url=f"/api/interviews/{session_id}/commands/cmd/stream",
        )

    def snapshot(self, session_id):
        self.snapshots.append(session_id)
        return {
            "session_id": session_id,
            "workflow_engine": "langgraph-v2",
        }

    def iter_sse(self, session_id, command_id, after_event_id=None):
        yield f"event: done\ndata: {session_id}:{command_id}\n\n"


def _runtime():
    store = InterviewSessionStore(llm=StubLLM())
    publisher = RecordingPublisher()
    jobs = RecordingJobs()
    workflow = WorkflowSpy()
    application = InterviewApplicationService(
        store=store,
        workflow_service_factory=lambda: workflow,
        publisher=publisher,
        report_job_store_factory=lambda: jobs,
    )
    turn = store.start(
        InterviewPlan(
            title="Application boundary",
            questions=[
                InterviewQuestion(
                    id="q1",
                    kind="technical",
                    prompt="Explain cache consistency.",
                    focus="cache consistency",
                )
            ],
        ),
        job_description="Backend role",
        resume_text="Built APIs",
        job_tags=["backend"],
    )
    return application, store, workflow, publisher, jobs, turn.session_id


def test_legacy_command_does_not_resolve_durable_workflow():
    application, store, workflow, _, jobs, session_id = _runtime()

    result = application.execute(
        SessionCommand.answer(
            session_id,
            "I used versioned cache keys.",
            expected_version=1,
            command_id="cmd-answer",
        )
    )

    assert result.kind == "legacy"
    assert result.turn is not None
    assert workflow.commands == []
    assert jobs.sessions == []
    assert store.get(session_id)["last_command_id"] == "cmd-answer"


def test_finish_command_publishes_transition_and_enqueues_report():
    application, _, _, publisher, jobs, session_id = _runtime()

    result = application.execute(
        SessionCommand(
            session_id=session_id,
            command_type="finish",
            expected_version=1,
            command_id="cmd-finish",
        )
    )

    assert result.turn is not None and result.turn.status == "finished"
    assert len(publisher.events) == 1
    assert publisher.events[0].causation_id == "cmd-finish"
    assert jobs.sessions == [session_id]


def test_durable_command_uses_workflow_without_mutating_legacy_store():
    application, store, workflow, _, jobs, session_id = _runtime()
    state = store.get(session_id)
    state["workflow_engine"] = "langgraph-v2"
    state["graph_schema_version"] = "langgraph-v2"

    result = application.execute(
        SessionCommand.answer(
            session_id,
            "I used a transactional outbox.",
            expected_version=1,
            command_id="cmd-durable",
        )
    )

    assert result.kind == "durable"
    assert result.accepted is not None
    assert workflow.commands[0][1]["command_type"] == "answer"
    assert store.get(session_id)["state_version"] == 1
    assert jobs.sessions == []


def test_snapshot_rejects_deleting_session_before_projection():
    application, store, workflow, _, _, session_id = _runtime()
    store.get(session_id)["deletion_status"] = "deleting"

    try:
        application.snapshot(session_id)
    except SessionDeletingError as exc:
        assert exc.session_id == session_id
    else:  # pragma: no cover - assertion guard
        raise AssertionError("deleting session was projected")

    assert workflow.snapshots == []


def test_legacy_stream_returns_typed_chunk_then_done_events():
    application, _, _, _, _, session_id = _runtime()
    result = StreamingTurnService(application).prepare(
        SessionCommand.answer(
            session_id,
            "I used versioned cache keys.",
            expected_version=1,
            command_id="cmd-stream",
        )
    )

    assert isinstance(result, LegacySessionStream)
    events = list(result.events)
    assert [event.event for event in events] == ["chunk", "chunk", "done"]


def test_durable_stream_preserves_workflow_event_iterator():
    application, store, workflow, _, _, session_id = _runtime()
    state = store.get(session_id)
    state["workflow_engine"] = "langgraph-v2"
    state["graph_schema_version"] = "langgraph-v2"

    result = StreamingTurnService(application).prepare(
        SessionCommand.answer(
            session_id,
            "I used a transactional outbox.",
            expected_version=1,
            command_id="cmd-stream-durable",
        )
    )

    assert isinstance(result, DurableSessionStream)
    assert "event: done" in "".join(result.events)
    assert workflow.commands[0][1]["command_id"] == "cmd-stream-durable"
