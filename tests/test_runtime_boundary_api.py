from fastapi.testclient import TestClient

from app.main import app


def test_runtime_boundary_endpoint_reports_stage_29_components(monkeypatch):
    monkeypatch.delenv("INTERVIEW_EVENT_BACKEND", raising=False)
    monkeypatch.delenv("AGENT_TRACE_DIR", raising=False)
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
        "langgraph": True,
    }
    assert body["orchestration"] == {
        "engine": "langgraph",
        "phase_aware": True,
        "resume_contract": "versioned_http",
    }
    assert body["agent_runtime"] == {
        "schema_version": "agent-runtime-v1",
        "event_schema_version": "runtime-event-v1",
        "trace_enabled": False,
        "outbox_enabled": body["runtime_store"] == "postgres",
        "agent_ledger_enabled": body["runtime_store"] == "postgres",
    }


def test_runtime_boundary_endpoint_reports_stage_29_celery_mode(monkeypatch):
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
        "langgraph": True,
    }
    assert body["orchestration"]["engine"] == "langgraph"


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
        "langgraph": True,
    }


def test_runtime_boundary_reports_agent_trace_enabled_without_exposing_path(
    monkeypatch,
):
    monkeypatch.setenv("AGENT_TRACE_DIR", "C:\\private\\agent-traces")
    client = TestClient(app)

    response = client.get("/api/runtime")

    assert response.status_code == 200
    body = response.json()
    assert body["agent_runtime"] == {
        "schema_version": "agent-runtime-v1",
        "event_schema_version": "runtime-event-v1",
        "trace_enabled": True,
        "outbox_enabled": body["runtime_store"] == "postgres",
        "agent_ledger_enabled": body["runtime_store"] == "postgres",
    }
    assert "C:\\private\\agent-traces" not in response.text
