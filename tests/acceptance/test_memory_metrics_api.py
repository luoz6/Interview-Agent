import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import runtime
from app.services.memory_metrics import get_memory_metric_store


@pytest.fixture
def isolated_runtime():
    runtime.reset_runtime_for_tests()
    try:
        yield
    finally:
        runtime.reset_runtime_for_tests()


def test_metrics_endpoint_is_hidden_then_returns_aggregate(
    monkeypatch,
    isolated_runtime,
):
    metrics = get_memory_metric_store()
    metrics.clear()
    client = TestClient(app)
    monkeypatch.delenv("MEMORY_TRUSTED_LOCAL_METRICS_ENABLED", raising=False)
    assert client.get("/api/runtime/memory-metrics").status_code == 404

    monkeypatch.setenv("MEMORY_TRUSTED_LOCAL_METRICS_ENABLED", "true")
    response = client.get(
        "/api/runtime/memory-metrics",
        params={"window_minutes": 60},
    )

    assert response.status_code == 200
    assert response.json()["schema_version"] == "memory-metrics-v1"
    assert response.json()["items"] == []
