from fastapi.testclient import TestClient

from app.main import app


def test_runtime_boundary_endpoint_reports_local_v1_components(monkeypatch):
    monkeypatch.delenv("INTERVIEW_EVENT_BACKEND", raising=False)
    client = TestClient(app)

    response = client.get("/api/runtime")

    assert response.status_code == 200
    body = response.json()
    assert body["runtime_store"] in {"memory", "postgres"}
    assert body["session_store"] in {
        "InterviewSessionStore",
        "PostgresInterviewSessionStore",
    }
    assert body["report_job_store"] == "PostgresReportJobStore"
    assert body["report_worker"] == "external_process"
    assert body["event_transport"] == {
        "interview": "sse",
        "report_progress": "polling",
    }
    assert body["event_backend"] == "local"
    assert body["capabilities"] == {
        "redis": False,
        "celery": False,
        "websocket": False,
        "langgraph": False,
    }
    assert "postgres:postgres" not in str(body)


def test_runtime_boundary_endpoint_reports_celery_round_review_mode(monkeypatch):
    monkeypatch.setenv("INTERVIEW_EVENT_BACKEND", "celery")
    client = TestClient(app)

    response = client.get("/api/runtime")

    assert response.status_code == 200
    body = response.json()
    assert body["event_backend"] == "celery"
    assert body["capabilities"] == {
        "redis": True,
        "celery": True,
        "websocket": False,
        "langgraph": False,
    }

def test_runtime_boundary_endpoint_reports_noop_event_mode(monkeypatch):
    monkeypatch.setenv("INTERVIEW_EVENT_BACKEND", "noop")
    client = TestClient(app)

    response = client.get("/api/runtime")

    assert response.status_code == 200
    body = response.json()
    assert body["event_backend"] == "noop"
    assert body["capabilities"] == {
        "redis": False,
        "celery": False,
        "websocket": False,
        "langgraph": False,
    }

