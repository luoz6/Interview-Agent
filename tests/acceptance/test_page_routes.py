"""HTTP acceptance tests for the API-only backend page boundary."""

from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_backend_root_describes_api_only_boundary():
    response = client.get("/")

    assert response.status_code == 200
    assert response.json() == {
        "service": "Interview Agent API",
        "status": "ok",
        "frontend": "http://127.0.0.1:5173",
        "docs": "/docs",
    }


def test_frontend_routes_are_not_served_by_fastapi():
    for path in (
        "/prep",
        "/interview?session_id=session-1",
        "/report-processing?session_id=session-1",
        "/report-detail?session_id=session-1",
        "/reports",
        "/help",
    ):
        response = client.get(path)
        assert response.status_code == 404


def test_vite_development_origin_is_allowed_by_cors():
    response = client.options(
        "/api/health",
        headers={
            "Origin": "http://127.0.0.1:5173",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:5173"
