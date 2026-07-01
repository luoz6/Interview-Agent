from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_health_endpoint():
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_prepare_endpoint_returns_questions():
    response = client.post(
        "/api/prep",
        json={
            "job_description": "Backend role using Python, FastAPI, Redis, and PostgreSQL.",
            "resume_text": "Built a FastAPI service with Redis cache.",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["title"] == "后端岗位模拟面试"
    assert len(body["questions"]) >= 3


def test_interview_answer_flow():
    start_response = client.post(
        "/api/interviews",
        json={
            "job_description": "Backend role using Python and Redis.",
            "resume_text": "Built a Python API with Redis.",
        },
    )

    assert start_response.status_code == 200
    started = start_response.json()
    assert started["session_id"]
    assert started["current_question"]

    answer_response = client.post(
        f"/api/interviews/{started['session_id']}/answer",
        json={"answer": "I used Redis to cache frequently requested records."},
    )

    assert answer_response.status_code == 200
    answered = answer_response.json()
    assert answered["session_id"] == started["session_id"]
    assert answered["follow_up"] or answered["current_question"]
