from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.a2a.invocation.local import LocalAgentInvoker
from app.a2a.contracts import (
    EvaluationArtifactPayload,
    EvaluationArtifactSetPayload,
    FollowupArtifactPayload,
)
from app.a2a.official_server import install_official_a2a_routes


def make_app_with_fake_examiner():
    invoker = LocalAgentInvoker()
    invoker.register(
        agent_id="interview-examiner",
        skill="generate-followup",
        handler=lambda request, execution_context: FollowupArtifactPayload(
            question_id=request.get("question_id", "q1"),
            followup_text="为什么选择 cache-aside？",
            reason_code="gap",
            policy_version="adaptive_v1",
        ),
    )
    app = FastAPI()
    install_official_a2a_routes(app, invoker=invoker)
    return app


def test_official_agent_card_endpoint_is_available():
    client = TestClient(make_app_with_fake_examiner())
    response = client.get("/.well-known/agent-card.json")
    assert response.status_code == 200
    payload = response.json()
    assert payload["name"] == "Interview Agent Platform"


def test_official_rest_routes_are_mounted_for_four_agents():
    app = make_app_with_fake_examiner()
    rest_paths = {
        getattr(route, "path", "")
        for route in app.routes
        if getattr(route, "path", "").startswith("/a2a/")
    }
    assert any("interview-examiner" in path for path in rest_paths)
    assert any("knowledge-and-grounding" in path for path in rest_paths)
    assert any("interview-reviewer" in path for path in rest_paths)
    assert any("report-coach" in path for path in rest_paths)


def test_each_professional_agent_has_discovery_card():
    client = TestClient(make_app_with_fake_examiner())
    for agent_id in (
        "interview-examiner",
        "knowledge-and-grounding",
        "interview-reviewer",
        "report-coach",
    ):
        response = client.get(
            f"/a2a/{agent_id}/.well-known/agent-card.json"
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["name"]
        assert payload["skills"]


def test_official_examiner_task_returns_completed_artifact():
    client = TestClient(make_app_with_fake_examiner())
    response = client.post(
        "/a2a/interview-examiner/message:send",
        headers={"A2A-Version": "1.0"},
        json={
            "message": {
                "message_id": "m1",
                "role": "ROLE_USER",
                "parts": [
                    {
                        "text": '{"skill":"generate-followup","input":{"question_id":"q1"}}'
                    }
                ],
            }
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["task"]["status"]["state"] == "TASK_STATE_COMPLETED"
    assert payload["task"]["artifacts"][0]["name"] == "followup-artifact"
    assert "q1" in payload["task"]["artifacts"][0]["parts"][0]["text"]


def test_examiner_local_and_official_http_parity():
    invoker = LocalAgentInvoker()
    expected = FollowupArtifactPayload(
        question_id="q1",
        followup_text="为什么选择 cache-aside？",
        reason_code="gap",
        policy_version="adaptive_v1",
    )
    invoker.register(
        agent_id="interview-examiner",
        skill="generate-followup",
        handler=lambda request, execution_context: expected,
    )
    app = FastAPI()
    install_official_a2a_routes(app, invoker=invoker)
    local_artifact = invoker.invoke(
        agent_id="interview-examiner",
        skill="generate-followup",
        request={"question_id": "q1"},
    )
    client = TestClient(app)
    response = client.post(
        "/a2a/interview-examiner/message:send",
        headers={"A2A-Version": "1.0"},
        json={
            "message": {
                "message_id": "m-parity",
                "role": "ROLE_USER",
                "parts": [
                    {
                        "text": '{"skill":"generate-followup","input":{"question_id":"q1"}}'
                    }
                ],
            }
        },
    )
    official_text = response.json()["task"]["artifacts"][0]["parts"][0]["text"]
    official_artifact = FollowupArtifactPayload.model_validate_json(official_text)
    assert official_artifact.question_id == local_artifact.question_id
    assert official_artifact.followup_text == local_artifact.followup_text


def test_official_reviewer_receives_execution_context():
    invoker = LocalAgentInvoker()
    seen = {}

    def reviewer_handler(request, execution_context):
        seen["execution_context"] = execution_context
        assert execution_context is not None
        assert execution_context.agent == "shadow_reviewer"
        assert execution_context.phase == "review"
        assert execution_context.session_id == "session-1"
        assert execution_context.correlation_id == "corr-1"
        return EvaluationArtifactSetPayload(
            evaluations=[
                EvaluationArtifactPayload(
                    question_id="q1",
                    score=80,
                    evaluation_policy_version="review-policy-v1",
                    evaluation_status="evaluated",
                )
            ]
        )

    invoker.register(
        agent_id="interview-reviewer",
        skill="evaluate-interview",
        handler=reviewer_handler,
    )
    app = FastAPI()
    install_official_a2a_routes(app, invoker=invoker)
    client = TestClient(app)
    response = client.post(
        "/a2a/interview-reviewer/message:send",
        headers={"A2A-Version": "1.0"},
        json={
            "message": {
                "message_id": "m-reviewer",
                "context_id": "session-1",
                "role": "ROLE_USER",
                "parts": [
                    {"text": '{"skill":"evaluate-interview","input":{"state":{}}}'}
                ],
            },
            "metadata": {"correlation_id": "corr-1"},
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["task"]["status"]["state"] == "TASK_STATE_COMPLETED"
    assert payload["task"]["artifacts"][0]["name"] == "evaluation-artifact-set"
    assert seen["execution_context"] is not None
