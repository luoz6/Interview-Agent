from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import app
from app.services.memory_metrics import (
    InMemoryMemoryMetricStore,
    MemoryMetricDimensions,
    MemoryMetricEvent,
    MemoryMetricValues,
    get_memory_metric_store,
    ResilientMemoryMetricStore,
)


def test_compression_eligibility_metric_accepts_approaching_budget_reason():
    event = MemoryMetricEvent(
        metric_code="compression_eligibility",
        dimensions=MemoryMetricDimensions(
            operation="followup",
            outcome="eligible",
            reason="approaching_operation_budget",
            policy_version="context-compression-eligibility-v1",
        ),
        values=MemoryMetricValues(
            estimated_input_tokens=9_088,
        ),
    )

    assert event.dimensions.reason == "approaching_operation_budget"


def test_metric_contract_rejects_content_ids_credentials_and_unknown_fields():
    forbidden = (
        "prompt",
        "answer",
        "summary",
        "excerpt",
        "session_id",
        "question_id",
        "evidence_id",
        "artifact_ref",
        "credential",
        "dsn",
        "principal_id",
        "fact_id",
        "normalized_fact",
        "source_manifest_sha256",
        "source_excerpt_sha256",
    )
    for key in forbidden:
        with pytest.raises(ValidationError, match="extra_forbidden"):
            MemoryMetricEvent.model_validate(
                {
                    "metric_code": "context_route",
                    "dimensions": {
                        "operation": "followup",
                        "route": "deterministic",
                        key: "PRIVATE-CONTENT",
                    },
                    "values": {},
                }
            )


def test_aggregate_is_windowed_and_marks_small_language_samples():
    now = datetime(2026, 7, 30, tzinfo=timezone.utc)
    store = InMemoryMemoryMetricStore(
        clock=lambda: now,
        minimum_language_samples=5,
    )
    store.publish(
        MemoryMetricEvent(
            metric_code="provider_usage",
            dimensions=MemoryMetricDimensions(
                operation="provider",
                language_bucket="zh_hans",
            ),
            values=MemoryMetricValues(
                estimated_input_tokens=120,
                provider_input_tokens=100,
            ),
            observed_at=now,
        )
    )

    result = store.aggregate(window_minutes=60)

    assert result["schema_version"] == "memory-metrics-v1"
    assert result["items"][0]["sample_status"] == "insufficient_sample"
    assert result["items"][0]["values"]["provider_input_tokens"] == 100
    assert "PRIVATE-CONTENT" not in repr(result)


def test_aggregate_rejects_unapproved_windows():
    with pytest.raises(ValueError, match="unsupported"):
        InMemoryMemoryMetricStore().aggregate(window_minutes=61)


def test_metrics_endpoint_is_hidden_then_returns_aggregate(monkeypatch):
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


def test_resilient_metrics_fail_open_and_mark_local_data_incomplete():
    class FailingStore:
        def publish(self, event):
            raise RuntimeError("database unavailable")

        def aggregate(self, *, window_minutes):
            raise RuntimeError("database unavailable")

    now = datetime(2026, 7, 30, tzinfo=timezone.utc)
    fallback = InMemoryMemoryMetricStore(clock=lambda: now)
    store = ResilientMemoryMetricStore(primary=FailingStore(), fallback=fallback)
    store.publish(
        MemoryMetricEvent(
            metric_code="context_route",
            dimensions=MemoryMetricDimensions(
                operation="followup",
                route="deterministic",
            ),
            observed_at=now,
        )
    )

    result = store.aggregate(window_minutes=60)

    assert result["store_kind"] == "process_local"
    assert result["durable_store_kind"] == "postgres_aggregate"
    assert result["data_complete"] is False
    assert result["items"][0]["values"]["event_count"] == 1
