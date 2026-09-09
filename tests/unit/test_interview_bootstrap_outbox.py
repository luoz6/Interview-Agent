from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import app.services.interview_workflow as workflow_module
from app.api.interview import routes as interview_routes
from app.services.interview_workflow import InterviewWorkflowService
from app.services.interview_workflow_consumer import InterviewWorkflowConsumer
from app.services.interview_workflow_store import PostgresInterviewWorkflowStore
from app.services.langgraph_runtime import VersionedGraphRegistry
from app.services.runtime_domain_events import InterviewBootstrapReadyEvent


class RecordingUnitOfWork:
    def __init__(self, calls):
        self.cursor = object()
        self.calls = calls
        self.committed = False
        self.exit_exception = None

    def __enter__(self):
        return self

    def commit(self):
        self.calls.append(("commit", self.cursor))
        self.committed = True

    def __exit__(self, exc_type, exc, traceback):
        self.exit_exception = exc
        return False


class RecordingSessionStore:
    def __init__(self, calls):
        self.calls = calls
        self.uow = RecordingUnitOfWork(calls)

    def unit_of_work(self):
        return self.uow

    def insert_session_in_transaction(self, cursor, **kwargs):
        self.calls.append(("session", cursor, kwargs))

    def get(self, session_id):
        return {"session_id": session_id}

    @staticmethod
    def _to_turn(state, follow_up):
        return state


class RecordingWorkflowStore:
    def __init__(self, calls, *, enqueue_error=None):
        self.calls = calls
        self.enqueue_error = enqueue_error

    def enqueue_bootstrap_with_cursor(self, cursor, session_id):
        self.calls.append(("bootstrap", cursor, session_id))
        if self.enqueue_error is not None:
            raise self.enqueue_error


def make_start_workflow(*, enqueue_error=None):
    calls = []
    session_store = RecordingSessionStore(calls)
    workflow_store = RecordingWorkflowStore(
        calls,
        enqueue_error=enqueue_error,
    )
    registry = VersionedGraphRegistry()
    registry.register("langgraph-v3", object())
    workflow = InterviewWorkflowService(
        legacy_store=session_store,
        workflow_store=workflow_store,
        generation_store=object(),
        graph_registry=registry,
        runtime_store="postgres",
        runtime_enabled=True,
        rollout_percent=100,
        default_graph_version="langgraph-v3",
    )
    return workflow, session_store, calls


def test_v3_shell_plan_binding_and_bootstrap_event_share_one_transaction():
    workflow, session_store, calls = make_start_workflow()
    plan = SimpleNamespace(schema_version="interview-plan-v3")
    plan_binding = object()
    workflow.ensure_interview_bootstrapped = lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("start must leave V3 bootstrap to the outbox consumer")
    )

    result = workflow.start(
        plan,
        job_description="backend role",
        resume_text="reliability experience",
        job_tags=["backend"],
        plan_binding=plan_binding,
        session_id="session-atomic",
        bootstrap=False,
    )

    cursor = session_store.uow.cursor
    assert [call[0] for call in calls] == ["session", "bootstrap", "commit"]
    assert calls[0][1] is cursor
    assert calls[0][2]["plan_binding"] is plan_binding
    assert calls[1] == ("bootstrap", cursor, "session-atomic")
    assert session_store.uow.committed is True
    assert result == {"session_id": "session-atomic"}


def test_v3_shell_is_not_committed_when_bootstrap_enqueue_fails():
    failure = RuntimeError("outbox unavailable")
    workflow, session_store, calls = make_start_workflow(enqueue_error=failure)

    with pytest.raises(RuntimeError, match="outbox unavailable"):
        workflow.start(
            SimpleNamespace(schema_version="interview-plan-v3"),
            job_description="backend role",
            resume_text="reliability experience",
            job_tags=["backend"],
            session_id="session-rollback",
            bootstrap=False,
        )

    assert [call[0] for call in calls] == ["session", "bootstrap"]
    assert session_store.uow.committed is False
    assert session_store.uow.exit_exception is failure


def test_bootstrap_event_identity_is_stable_for_duplicate_enqueue():
    class Control:
        def __init__(self):
            self.events = []

        def enqueue_event(self, cursor, event):
            self.events.append((cursor, event))

    cursor = object()
    control = Control()
    store = PostgresInterviewWorkflowStore.__new__(
        PostgresInterviewWorkflowStore
    )
    store.control = control

    store.enqueue_bootstrap_with_cursor(cursor, "session-duplicate")
    store.enqueue_bootstrap_with_cursor(cursor, "session-duplicate")

    assert [event.event_id for _, event in control.events] == [
        "interview-bootstrap-session-duplicate",
        "interview-bootstrap-session-duplicate",
    ]
    assert all(used_cursor is cursor for used_cursor, _ in control.events)


class TransitioningGraph:
    def __init__(self):
        self.values = {}
        self.invoke_count = 0

    def get_state(self, config):
        return SimpleNamespace(values=dict(self.values), next=())

    def invoke(self, initial_state, *, config):
        self.invoke_count += 1
        self.values = {**initial_state, "interview_status": "active"}
        return dict(self.values)


class PartialBootstrapGraph:
    def __init__(self):
        self.values = {
            "session_id": "session-partial",
            "workflow_engine": "langgraph-v3",
            "interview_status": "preparing_first_question",
        }
        self.next = ("generate_main_question",)
        self.invocations = []

    def get_state(self, config):
        return SimpleNamespace(values=dict(self.values), next=self.next)

    def invoke(self, graph_input, *, config):
        self.invocations.append(graph_input)
        self.values = {**self.values, "interview_status": "active"}
        self.next = ("wait_for_answer",)
        return dict(self.values)


class BootstrapSessionStore:
    def get(self, session_id):
        return {
            "session_id": session_id,
            "workflow_engine": "langgraph-v3",
            "graph_schema_version": "langgraph-v3",
            "plan": object(),
            "job_description": "backend role",
            "resume_text": "reliability experience",
            "job_tags": ["backend"],
        }


class BootstrapRegistrationStore:
    def __init__(self):
        self.registrations = []

    def register_bootstrap_input(self, **kwargs):
        self.registrations.append(kwargs)


def test_duplicate_bootstrap_delivery_does_not_invoke_graph_twice(monkeypatch):
    monkeypatch.setattr(
        workflow_module,
        "session_plan_binding_from_state",
        lambda state: object(),
    )
    monkeypatch.setattr(
        workflow_module,
        "make_durable_initial_state_v3",
        lambda session_id, plan, **kwargs: {
            "session_id": session_id,
            "workflow_engine": "langgraph-v3",
        },
    )
    graph = TransitioningGraph()
    registry = VersionedGraphRegistry()
    registry.register("langgraph-v3", graph)
    registration_store = BootstrapRegistrationStore()
    workflow = InterviewWorkflowService(
        legacy_store=BootstrapSessionStore(),
        workflow_store=registration_store,
        generation_store=object(),
        graph_registry=registry,
        runtime_store="postgres",
        runtime_enabled=True,
        rollout_percent=100,
        default_graph_version="langgraph-v3",
    )
    consumer = InterviewWorkflowConsumer(workflow)
    payload = InterviewBootstrapReadyEvent(
        event_id="interview-bootstrap-session-redelivery",
        session_id="session-redelivery",
    ).model_dump(mode="json")

    first = consumer.consume(payload)
    second = consumer.consume(payload)

    assert first.status == second.status == "completed"
    assert graph.invoke_count == 1
    assert len(registration_store.registrations) == 2
    assert registration_store.registrations[0]["bootstrap_input_sha256"] == (
        registration_store.registrations[1]["bootstrap_input_sha256"]
    )
    assert registration_store.registrations[0]["require_unstarted"] is True
    assert registration_store.registrations[1]["require_unstarted"] is False


def test_bootstrap_redelivery_resumes_partial_v3_checkpoint(monkeypatch):
    monkeypatch.setattr(
        workflow_module,
        "session_plan_binding_from_state",
        lambda state: object(),
    )
    monkeypatch.setattr(
        workflow_module,
        "make_durable_initial_state_v3",
        lambda session_id, plan, **kwargs: {
            "session_id": session_id,
            "workflow_engine": "langgraph-v3",
        },
    )
    graph = PartialBootstrapGraph()
    registry = VersionedGraphRegistry()
    registry.register("langgraph-v3", graph)
    workflow = InterviewWorkflowService(
        legacy_store=BootstrapSessionStore(),
        workflow_store=BootstrapRegistrationStore(),
        generation_store=object(),
        graph_registry=registry,
        runtime_store="postgres",
        runtime_enabled=True,
        rollout_percent=100,
        default_graph_version="langgraph-v3",
    )

    result = workflow.bootstrap_first_question("session-partial")

    assert result["interview_status"] == "active"
    assert graph.invocations == [None]


def test_bootstrap_redelivery_does_not_resume_active_wait_checkpoint(monkeypatch):
    monkeypatch.setattr(
        workflow_module,
        "session_plan_binding_from_state",
        lambda state: object(),
    )
    monkeypatch.setattr(
        workflow_module,
        "make_durable_initial_state_v3",
        lambda session_id, plan, **kwargs: {
            "session_id": session_id,
            "workflow_engine": "langgraph-v3",
        },
    )
    graph = PartialBootstrapGraph()
    graph.values["interview_status"] = "active"
    graph.next = ("wait_for_answer",)
    registry = VersionedGraphRegistry()
    registry.register("langgraph-v3", graph)
    workflow = InterviewWorkflowService(
        legacy_store=BootstrapSessionStore(),
        workflow_store=BootstrapRegistrationStore(),
        generation_store=object(),
        graph_registry=registry,
        runtime_store="postgres",
        runtime_enabled=True,
        rollout_percent=100,
        default_graph_version="langgraph-v3",
    )

    result = workflow.bootstrap_first_question("session-partial")

    assert result["interview_status"] == "active"
    assert graph.invocations == []


def test_bootstrap_sse_only_replays_background_committed_question(monkeypatch):
    class Store:
        def get(self, session_id):
            return {
                "session_id": session_id,
                "workflow_engine": "langgraph-v3",
            }

    class Workflow:
        def bootstrap_first_question(self, session_id):
            raise AssertionError("SSE must not drive bootstrap")

        def snapshot(self, session_id):
            return {
                "session_id": session_id,
                "status": "active",
                "state_version": 2,
                "current_question": {
                    "id": "q1",
                    "prompt": "你如何处理 Redis 与消息队列之间的一致性？",
                    "generation_id": "generation-q1",
                },
            }

    monkeypatch.setattr(
        interview_routes.dependencies,
        "get_interview_workflow_service",
        lambda: Workflow(),
    )
    response = interview_routes.stream_interview_bootstrap(
        "session-background",
        store=Store(),
    )

    async def collect_body():
        chunks = []
        async for chunk in response.body_iterator:
            chunks.append(chunk.decode() if isinstance(chunk, bytes) else chunk)
        return "".join(chunks)

    body = asyncio.run(collect_body())

    assert "question_reveal_done" in body
    assert "generation-q1" in body
    assert "bootstrap_failed" not in body
