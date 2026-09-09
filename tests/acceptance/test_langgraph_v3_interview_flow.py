"""In-memory acceptance for the formal V3 API and Graph boundaries.

This suite does not validate the production PostgreSQL transaction, Outbox, or
Consumer. Those durability guarantees belong to protected PostgreSQL tests.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from time import monotonic, sleep
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api.interview import routes as interview_routes
from app.api.shared import dependencies
from app.graphs.interview_state import build_v3_session_shell_state
from app.main import app
from app.services.event_publisher import NoopRuntimeEventPublisher
from app.services.interview_plan_revision import PlanSourcePayload
from app.services.interview_plan_revision_store import (
    InMemoryInterviewPlanRevisionStore,
)
from app.services.session import InterviewSessionStore
from tests.browser_v3_runtime import BrowserV3Harness


class _UnusedReportQueue:
    def enqueue_report_request(self, _session_id: str) -> None:
        raise AssertionError("V3 acceptance flow must not enqueue a local report")


class _AcceptanceUnitOfWork:
    """Minimum transaction-shaped port required by workflow.start()."""

    def __init__(self) -> None:
        self.cursor = SimpleNamespace(bootstrap_session_ids=[])

    def commit(self) -> None:
        return None


class _AcceptanceSessionStore(InterviewSessionStore):
    """Adapts the in-memory Store to the V3 workflow's persistence port."""

    @contextmanager
    def unit_of_work(self):
        unit_of_work = _AcceptanceUnitOfWork()
        yield unit_of_work

    def insert_session_in_transaction(
        self,
        _cursor,
        *,
        session_id,
        plan,
        job_description,
        resume_text,
        job_tags,
        memory_policy_version,
        plan_binding,
        **_kwargs,
    ) -> None:
        self._sessions[session_id] = build_v3_session_shell_state(
            session_id=session_id,
            plan=plan,
            job_description=job_description,
            resume_text=resume_text,
            job_tags=job_tags,
            memory_policy_version=memory_policy_version,
            plan_binding=plan_binding,
        )


@pytest.fixture
def v3_acceptance_api(monkeypatch):
    previous_dependency_overrides = dict(app.dependency_overrides)
    session_store = _AcceptanceSessionStore()
    harness = BrowserV3Harness(session_store)
    publisher = NoopRuntimeEventPublisher()
    revision_store = InMemoryInterviewPlanRevisionStore()
    frozen_plan = harness._plan(fallback=False)
    source = PlanSourcePayload(
        job_description="Synthetic backend engineer role",
        resume_text="Synthetic Redis and messaging project",
        job_tags=["Redis", "RocketMQ"],
    )
    revision = revision_store.create_initial(
        source_payload=source,
        plan=frozen_plan,
        retention_policy="local-v1",
        generator_version=frozen_plan.configuration_snapshot.generator_version,
    )

    def enqueue_bootstrap_with_cursor(cursor, session_id):
        cursor.bootstrap_session_ids.append(session_id)

    harness.workflow_store.enqueue_bootstrap_with_cursor = (
        enqueue_bootstrap_with_cursor
    )
    app.dependency_overrides[dependencies.get_session_store] = (
        lambda: session_store
    )
    app.dependency_overrides[dependencies.get_event_publisher] = (
        lambda: publisher
    )
    app.dependency_overrides[dependencies.get_plan_revision_store] = (
        lambda: revision_store
    )
    app.dependency_overrides[dependencies.get_request_plan_revision_store] = (
        lambda: revision_store
    )
    app.dependency_overrides[
        dependencies.get_legacy_interview_start_service
    ] = lambda: SimpleNamespace(store=session_store)
    app.dependency_overrides[
        dependencies.get_request_interview_launch_coordinator
    ] = lambda: None
    app.dependency_overrides[
        dependencies.get_request_principal_memory_control_store
    ] = lambda: None
    app.dependency_overrides[
        dependencies.get_request_principal_identity_resolver
    ] = lambda: None
    app.dependency_overrides[
        dependencies.get_interview_knowledge_scope_resolver_factory
    ] = lambda: None
    app.dependency_overrides[dependencies.get_principal_identity_resolver] = (
        lambda: None
    )
    app.dependency_overrides[
        dependencies.get_user_materials_runtime_settings
    ] = lambda: SimpleNamespace(enabled=False, ingest_enabled=False)
    monkeypatch.setattr(
        dependencies,
        "get_interview_workflow_service",
        lambda: harness.workflow,
    )
    monkeypatch.setattr(
        dependencies,
        "get_report_job_store",
        lambda: _UnusedReportQueue(),
    )
    monkeypatch.setattr(
        interview_routes,
        "environment_value",
        lambda name, default=None: (
            "true" if name == "INTERVIEW_JIT_MAIN_QUESTION_ENABLED" else default
        ),
    )
    monkeypatch.setattr(interview_routes, "get_runtime_store", lambda: "postgres")
    monkeypatch.setattr(
        interview_routes,
        "get_interview_langgraph_rollout_percent",
        lambda: 100,
    )

    payload = {
        "plan_revision_id": revision.plan_revision_id,
        "expected_revision": revision.revision,
        "plan_sha256": revision.plan_sha256,
        "request_id": "v3-acceptance-start",
    }
    client = TestClient(app)
    try:
        yield client, harness, frozen_plan, payload
    finally:
        cleanup_error = None
        session_ids = set(harness.session_ids) | set(session_store._sessions)
        for thread in tuple(harness.bootstrap_threads.values()):
            try:
                thread.join(timeout=2)
                if thread.is_alive():
                    raise RuntimeError("V3 acceptance bootstrap thread leaked")
            except Exception as exc:  # pragma: no cover - teardown protection
                cleanup_error = cleanup_error or exc
        for session_id in session_ids:
            try:
                harness.delete(session_id)
            except Exception as exc:  # pragma: no cover - teardown protection
                cleanup_error = cleanup_error or exc
            try:
                session_store.delete_session(session_id)
            except Exception as exc:  # pragma: no cover - teardown protection
                cleanup_error = cleanup_error or exc
        try:
            client.close()
        except Exception as exc:  # pragma: no cover - teardown protection
            cleanup_error = cleanup_error or exc
        try:
            publisher.shutdown()
        except Exception as exc:  # pragma: no cover - teardown protection
            cleanup_error = cleanup_error or exc
        finally:
            app.dependency_overrides.clear()
            app.dependency_overrides.update(previous_dependency_overrides)
        if cleanup_error is not None:
            raise cleanup_error


def _wait_for_snapshot(client: TestClient, session_id: str, predicate):
    deadline = monotonic() + 3
    last_snapshot = None
    while monotonic() < deadline:
        response = client.get(f"/api/interviews/{session_id}")
        assert response.status_code == 200, response.text
        last_snapshot = response.json()
        if predicate(last_snapshot):
            return last_snapshot
        sleep(0.01)
    raise AssertionError(f"V3 snapshot did not converge: {last_snapshot}")


def _create_active_session(client: TestClient, harness, frozen_plan, payload):
    accepted = client.post("/api/interviews", json=payload)
    assert accepted.status_code == 202, accepted.text
    body = accepted.json()
    assert body["status"] == "preparing_first_question"
    assert body["workflow_engine"] == "langgraph-v3"
    assert body["status_url"].endswith(body["session_id"])
    assert body["current_question"] is None
    # Acceptance explicitly dispatches the in-memory Graph. Production
    # Outbox/Consumer delivery is outside this suite's evidence boundary.
    harness.session_ids.add(body["session_id"])
    harness.workflow.ensure_interview_bootstrapped(
        body["session_id"],
        plan=frozen_plan,
    )
    snapshot = _wait_for_snapshot(
        client,
        body["session_id"],
        lambda current: (current.get("current_question") or {}).get("id") == "q1",
    )
    return body, snapshot


def test_v3_in_memory_api_and_graph_generate_only_the_current_question(
    v3_acceptance_api,
):
    client, harness, frozen_plan, payload = v3_acceptance_api
    accepted, first = _create_active_session(
        client,
        harness,
        frozen_plan,
        payload,
    )
    session_id = accepted["session_id"]

    assert first["status"] == "active"
    assert first["workflow_engine"] == "langgraph-v3"
    assert first["graph_schema_version"] == "langgraph-v3"
    assert first["plan_snapshot"]["schema_version"] == "interview-plan-v3"
    assert first["current_question"]["id"] == "q1"
    assert "Redis" in first["current_question"]["prompt"]
    assert first["current_question"]["generation_id"]
    for intent in first["plan_snapshot"]["questions"]:
        assert not {"prompt", "text", "question_text"}.intersection(intent)

    graph_config = {"configurable": {"thread_id": session_id}}
    graph = harness.workflow.graph_for_session(session_id)
    first_values = graph.get_state(graph_config).values
    first_rendered = first_values["rendered_questions"]
    assert set(first_rendered) == {"q1"}
    assert (
        first_rendered["q1"]["generation_id"]
        == first["current_question"]["generation_id"]
    )
    assert len(harness.examiner.calls) == 1
    assert harness.examiner.calls[0]["intent"].question_id == "q1"
    assert "RocketMQ 重试与死信" not in json.dumps(
        harness.examiner.calls[0]["conversation"],
        ensure_ascii=False,
    )

    answered = client.post(
        f"/api/interviews/{session_id}/answer",
        json={
            "answer": "我会使用事务消息、补偿任务和对账，并保证消费幂等。",
            "expected_version": first["state_version"],
            "command_id": "v3-acceptance-answer-q1",
        },
    )
    assert answered.status_code == 202, answered.text
    second = _wait_for_snapshot(
        client,
        session_id,
        lambda current: (current.get("current_question") or {}).get("id") == "q2",
    )

    assert second["current_question"]["id"] == "q2"
    assert "RocketMQ" in second["current_question"]["prompt"]
    assert second["current_question"]["generation_id"] != (
        first["current_question"]["generation_id"]
    )
    second_values = graph.get_state(graph_config).values
    assert set(second_values["rendered_questions"]) == {"q1", "q2"}
    assert second_values["rendered_questions"]["q1"]["text"] == (
        first["current_question"]["prompt"]
    )
    assert len(harness.examiner.calls) == 2
    assert harness.examiner.calls[1]["intent"].question_id == "q2"
    assert "十倍流量扩容" not in json.dumps(
        harness.examiner.calls[1]["conversation"],
        ensure_ascii=False,
    )


def test_v3_in_memory_api_and_graph_can_finish_early(v3_acceptance_api):
    client, harness, frozen_plan, payload = v3_acceptance_api
    accepted, active = _create_active_session(
        client,
        harness,
        frozen_plan,
        payload,
    )
    session_id = accepted["session_id"]

    finished = client.post(
        f"/api/interviews/{session_id}/finish",
        json={
            "expected_version": active["state_version"],
            "command_id": "v3-acceptance-finish",
        },
    )
    assert finished.status_code == 202, finished.text
    final_snapshot = _wait_for_snapshot(
        client,
        session_id,
        lambda current: current.get("status") == "finished",
    )

    assert final_snapshot["status"] == "finished"
    graph = harness.workflow.graph_for_session(session_id)
    graph_snapshot = graph.get_state(
        {"configurable": {"thread_id": session_id}}
    )
    assert graph_snapshot.next == ()
    assert graph_snapshot.values["interview_status"] == "finished"
