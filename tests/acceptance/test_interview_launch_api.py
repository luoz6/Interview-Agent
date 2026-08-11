from uuid import uuid4

from fastapi.testclient import TestClient

from app.api.prep import routes as prep_route_module
import app.api.shared.dependencies as api_dependencies
from app.main import app
from app.services.in_memory_interview_launch_repository import (
    InMemoryInterviewLaunchRepository,
)
from app.services.in_memory_prep_plan_store import InMemoryPrepPlanStore
from app.services.interview_launch import InterviewLaunchCoordinator
from app.services.session import InterviewSessionStore
from tests.interview_fixtures import sample_interview_plan


def test_new_api_uses_authoritative_plan_without_preparing_twice(monkeypatch):
    plans = InMemoryPrepPlanStore()
    sessions = InterviewSessionStore()
    launches = InMemoryInterviewLaunchRepository()
    coordinator = InterviewLaunchCoordinator(
        prep_plan_store=plans,
        session_store=sessions,
        launch_repository=launches,
    )
    calls = {"count": 0}

    def prepare(*_args, **_kwargs):
        calls["count"] += 1
        return sample_interview_plan()

    monkeypatch.setattr(prep_route_module, "prepare_interview", prepare)
    app.dependency_overrides[api_dependencies.get_prep_plan_store] = lambda: plans
    monkeypatch.setattr(
        api_dependencies,
        "get_interview_launch_coordinator",
        lambda: coordinator,
    )
    client = TestClient(app)
    try:
        prepared = client.post(
            "/api/prep",
            json={"job_description": "Backend role", "resume_text": "Built systems"},
        ).json()
        response = client.post(
            "/api/interviews",
            json={
                "plan_id": prepared["plan_id"],
                "expected_plan_version": prepared["plan_version"],
                "command_id": f"start_{uuid4()}",
            },
        )
        assert response.status_code == 200
        assert calls["count"] == 1
        snapshot = sessions.snapshot(response.json()["session_id"])
        assert [item["prompt"] for item in snapshot["questions"]] == [
            item["prompt"] for item in prepared["questions"]
        ]
    finally:
        app.dependency_overrides.clear()


def test_launch_dependency_uses_same_overridden_session_store_as_session_routes(
    monkeypatch,
):
    plans = InMemoryPrepPlanStore()
    route_sessions = InterviewSessionStore()
    runtime_sessions = InterviewSessionStore()
    launches = InMemoryInterviewLaunchRepository()
    runtime_coordinator = InterviewLaunchCoordinator(
        prep_plan_store=plans,
        session_store=runtime_sessions,
        launch_repository=launches,
    )
    monkeypatch.setattr(
        prep_route_module,
        "prepare_interview",
        lambda *_args, **_kwargs: sample_interview_plan(),
    )
    monkeypatch.setattr(
        api_dependencies,
        "get_interview_launch_coordinator",
        lambda: runtime_coordinator,
    )
    app.dependency_overrides[api_dependencies.get_prep_plan_store] = lambda: plans
    app.dependency_overrides[api_dependencies.get_session_store] = (
        lambda: route_sessions
    )
    client = TestClient(app)
    try:
        prepared = client.post(
            "/api/prep",
            json={"job_description": "Backend role", "resume_text": "Built systems"},
        ).json()
        launched = client.post(
            "/api/interviews",
            json={
                "plan_id": prepared["plan_id"],
                "expected_plan_version": prepared["plan_version"],
                "command_id": f"start_{uuid4()}",
            },
        )
        assert launched.status_code == 200
        session_id = launched.json()["session_id"]
        assert client.get(f"/api/interviews/{session_id}").status_code == 200
        assert session_id in route_sessions._sessions
        assert runtime_sessions._sessions == {}
    finally:
        app.dependency_overrides.clear()
