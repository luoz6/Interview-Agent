from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api.interview import routes as interview_routes
from app.api.shared import dependencies
from app.graphs.interview_state import build_v3_session_shell_state
from app.main import app
from app.services.interview_plan_revision import v2_plan_to_v3
from app.services.interview_plan_revision_store import (
    InMemoryInterviewPlanRevisionStore,
)
from app.services.session import InterviewSessionStore
from app.services.in_memory_principal_memory_control import (
    InMemoryPrincipalMemoryControlStore,
)
from app.services.principal_identity import ExplicitPrincipalIdentityResolver
from tests.unit.test_interview_plan_revision import plan, source


class RecordingV3Workflow:
    def __init__(self, session_store):
        self.starts = []
        self.session_store = session_store
        self.snapshot_status = "preparing_first_question"

    def start(self, frozen_plan, **kwargs):
        self.starts.append((frozen_plan, kwargs))
        self.session_store._sessions[kwargs["session_id"]] = (
            build_v3_session_shell_state(
                session_id=kwargs["session_id"],
                plan=frozen_plan,
                job_description=kwargs["job_description"],
                resume_text=kwargs["resume_text"],
                job_tags=kwargs["job_tags"],
                memory_policy_version="question-conversation-v1",
                plan_binding=kwargs["plan_binding"],
            )
        )
        return SimpleNamespace(session_id=kwargs["session_id"])

    def snapshot(self, session_id):
        return {
            "session_id": session_id,
            "status": self.snapshot_status,
            "current_question": None,
        }


@pytest.fixture
def v3_start_api(monkeypatch):
    revision_store = InMemoryInterviewPlanRevisionStore()
    frozen_plan = v2_plan_to_v3(plan())
    revision = revision_store.create_initial(
        source_payload=source(),
        plan=frozen_plan,
        retention_policy="local-v1",
        generator_version=frozen_plan.configuration_snapshot.generator_version,
    )
    session_store = InterviewSessionStore()
    workflow = RecordingV3Workflow(session_store)
    app.dependency_overrides[dependencies.get_plan_revision_store] = (
        lambda: revision_store
    )
    app.dependency_overrides[
        dependencies.get_request_plan_revision_store
    ] = lambda: revision_store
    app.dependency_overrides[dependencies.get_session_store] = (
        lambda: session_store
    )
    app.dependency_overrides[
        dependencies.get_legacy_interview_start_service
    ] = lambda: SimpleNamespace(store=session_store)
    app.dependency_overrides[
        dependencies.get_interview_workflow_service
    ] = lambda: workflow
    monkeypatch.setattr(
        dependencies,
        "get_interview_workflow_service",
        lambda: workflow,
    )
    app.dependency_overrides[
        dependencies.get_principal_memory_control_store
    ] = lambda: InMemoryPrincipalMemoryControlStore()
    app.dependency_overrides[
        dependencies.get_principal_identity_resolver
    ] = lambda: ExplicitPrincipalIdentityResolver(
        deployment_id="v3-start-test",
        principal_id="test-principal",
    )
    app.dependency_overrides[
        dependencies.get_request_principal_memory_control_store
    ] = lambda: InMemoryPrincipalMemoryControlStore()
    app.dependency_overrides[
        dependencies.get_request_principal_identity_resolver
    ] = lambda: ExplicitPrincipalIdentityResolver(
        deployment_id="v3-start-test",
        principal_id="test-principal",
    )
    app.dependency_overrides[
        dependencies.get_interview_knowledge_scope_resolver_factory
    ] = lambda: None
    app.dependency_overrides[
        dependencies.get_user_materials_runtime_settings
    ] = lambda: SimpleNamespace(enabled=False, ingest_enabled=False)
    app.dependency_overrides[
        dependencies.get_request_interview_launch_coordinator
    ] = lambda: None
    payload = {
        "plan_revision_id": revision.plan_revision_id,
        "expected_revision": revision.revision,
        "plan_sha256": revision.plan_sha256,
        "request_id": "v3-start-contract",
    }
    try:
        yield TestClient(app), workflow, payload, monkeypatch
    finally:
        app.dependency_overrides.clear()


def _set_jit_capability(monkeypatch, enabled):
    monkeypatch.setattr(
        interview_routes,
        "environment_value",
        lambda name, default=None: (
            "true"
            if name == "INTERVIEW_JIT_MAIN_QUESTION_ENABLED" and enabled
            else "false"
            if name == "INTERVIEW_JIT_MAIN_QUESTION_ENABLED"
            else default
        ),
    )


def test_v3_start_is_rejected_when_jit_capability_is_disabled(v3_start_api):
    client, workflow, payload, monkeypatch = v3_start_api
    _set_jit_capability(monkeypatch, False)

    response = client.post("/api/interviews", json=payload)

    assert response.status_code == 503
    assert response.json()["detail"] == (
        "JIT main-question session creation is disabled"
    )
    assert workflow.starts == []


@pytest.mark.parametrize(
    ("runtime_store", "rollout_percent"),
    [("memory", 100), ("postgres", 0)],
)
def test_v3_start_fails_closed_when_durable_runtime_is_unavailable(
    v3_start_api,
    runtime_store,
    rollout_percent,
):
    client, workflow, payload, monkeypatch = v3_start_api
    _set_jit_capability(monkeypatch, True)
    monkeypatch.setattr(interview_routes, "get_runtime_store", lambda: runtime_store)
    monkeypatch.setattr(
        interview_routes,
        "get_interview_langgraph_rollout_percent",
        lambda: rollout_percent,
    )

    response = client.post("/api/interviews", json=payload)

    assert response.status_code == 503
    assert response.json()["detail"] == "langgraph-v3 runtime is unavailable"
    assert workflow.starts == []


def test_v3_start_returns_202_and_leaves_bootstrap_to_outbox(v3_start_api):
    client, workflow, payload, monkeypatch = v3_start_api
    _set_jit_capability(monkeypatch, True)
    monkeypatch.setattr(interview_routes, "get_runtime_store", lambda: "postgres")
    monkeypatch.setattr(
        interview_routes,
        "get_interview_langgraph_rollout_percent",
        lambda: 100,
    )

    response = client.post("/api/interviews", json=payload)

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["status"] == "preparing_first_question"
    assert body["workflow_engine"] == "langgraph-v3"
    assert body["stream_url"].endswith("/bootstrap/stream")
    assert body["status_url"].endswith(body["session_id"])
    assert len(workflow.starts) == 1
    frozen_plan, kwargs = workflow.starts[0]
    assert frozen_plan.schema_version == "interview-plan-v3"
    assert kwargs["bootstrap"] is False
    assert kwargs["session_id"] == body["session_id"]


def test_v3_start_replay_preserves_202_bootstrap_contract(v3_start_api):
    client, workflow, payload, monkeypatch = v3_start_api
    _set_jit_capability(monkeypatch, True)
    monkeypatch.setattr(interview_routes, "get_runtime_store", lambda: "postgres")
    monkeypatch.setattr(
        interview_routes,
        "get_interview_langgraph_rollout_percent",
        lambda: 100,
    )

    first = client.post("/api/interviews", json=payload)
    _set_jit_capability(monkeypatch, False)
    workflow.snapshot_status = "active"
    replay = client.post("/api/interviews", json=payload)

    assert first.status_code == 202, first.text
    assert replay.status_code == 202, replay.text
    assert replay.json() == {
        "session_id": first.json()["session_id"],
        "status": "active",
        "stream_url": first.json()["stream_url"],
        "status_url": first.json()["status_url"],
        "workflow_engine": "langgraph-v3",
        "current_question": None,
    }
    assert len(workflow.starts) == 1
