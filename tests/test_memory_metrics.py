from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import app
from app.services import memory_metrics as memory_metrics_module
from app.services.memory_metrics import (
    InMemoryMemoryMetricStore,
    MemoryMetricDimensions,
    MemoryMetricEvent,
    MemoryMetricValues,
    get_memory_metric_store,
    ResilientMemoryMetricStore,
)


def _compression_observation_payload(**changes):
    payload = {
        "measurement_path": "business",
        "operation": "question_conversation",
        "workflow": "interview",
        "policy_version": "conversation-v1",
        "intent_schema_version": "compression-intent-v1",
        "eligibility_reason": "approaching_operation_budget",
        "route": "artifact_created",
        "source_token_bucket": "2049_4096",
        "target_token_bucket": "513_1024",
        "result_token_bucket": "257_512",
        "compression_ratio_bucket": "0_2500_bp",
        "estimated_input_tokens": 3_000,
        "provider_input_tokens_when_available": 3_060,
        "provider_usage_available": True,
        "estimator_error_basis_points": 196,
        "source_demand_token_bucket": "2049_4096",
        "duplicate_removed_token_bucket": "1_256",
        "post_dedup_demand_token_bucket": "2049_4096",
        "mandatory_bounded_raw_token_bucket": "257_512",
        "pre_dedup_required_token_bucket": "1025_2048",
        "post_dedup_required_token_bucket": "1025_2048",
        "business_pre_loss_required_token_bucket": "1025_2048",
        "shadow_post_dedup_required_token_bucket": "1025_2048",
        "business_utilization_basis_points": 8_200,
        "shadow_post_dedup_utilization_basis_points": 7_600,
        "selected_unit_count": 4,
        "dropped_unit_count": 1,
        "truncated_unit_count": 2,
        "deduplicated_unit_count": 3,
        "exact_recent_preserved": True,
        "current_answer_preserved": True,
        "validation_outcome": "valid",
        "fallback_outcome": "not_used",
        "provider_circuit_state": "closed",
        "validation_quarantine_state": "closed",
        "failure_state_store_outcome": "available",
        "latency_bucket": "100_499_ms",
        "language_bucket": "mixed",
    }
    payload.update(changes)
    return payload


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
        "state_key_sha256",
        "privacy_scope_sha256",
        "owner_key_sha256",
        "probe_owner_sha256",
        "probe_token",
        "failure_state_record",
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
                provider_usage_available=True,
                estimator_error_basis_points=2_000,
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
    assert result["items"][0]["values"][
        "provider_input_tokens_when_available"
    ] == 100
    assert result["items"][0]["dimensions"][
        "estimator_error_basis_points"
    ] == 2_000
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


def test_compression_observation_maps_to_fixed_metric_columns(monkeypatch):
    observation_type = getattr(
        memory_metrics_module,
        "CompressionObservation",
        None,
    )
    publisher = getattr(
        memory_metrics_module,
        "publish_compression_observation",
        None,
    )
    assert observation_type is not None, (
        "Task 9 RED: CompressionObservation is not implemented"
    )
    assert publisher is not None, (
        "Task 9 RED: publish_compression_observation is not implemented"
    )
    published = []

    class CaptureStore:
        def publish(self, event):
            published.append(MemoryMetricEvent.model_validate(event))

    monkeypatch.setattr(
        memory_metrics_module,
        "get_memory_metric_store",
        lambda: CaptureStore(),
    )
    observation = observation_type.model_validate(
        _compression_observation_payload()
    )

    publisher(observation)

    assert len(published) == 1
    event = published[0]
    assert event.metric_code == "context_compression"
    assert event.dimensions.measurement_path == "business"
    assert event.dimensions.workflow == "interview"
    assert event.dimensions.language_bucket == "mixed"
    assert event.dimensions.estimator_error_basis_points == 196
    assert event.dimensions.exact_recent_preserved is True
    assert event.dimensions.current_answer_preserved is True
    assert event.values.estimated_input_tokens == 3_000
    assert event.values.provider_input_tokens == 3_060
    assert event.values.selected_count == 4
    assert event.values.dropped_count == 1
    assert event.values.truncated_count == 2
    assert event.values.source_count == 3


def test_compression_aggregate_projects_semantic_aliases_and_separates_paths(
    monkeypatch,
):
    observation_type = getattr(
        memory_metrics_module,
        "CompressionObservation",
        None,
    )
    publisher = getattr(
        memory_metrics_module,
        "publish_compression_observation",
        None,
    )
    assert observation_type is not None, (
        "Task 9 RED: CompressionObservation is not implemented"
    )
    assert publisher is not None, (
        "Task 9 RED: publish_compression_observation is not implemented"
    )
    now = datetime.now(timezone.utc)
    store = InMemoryMemoryMetricStore(clock=lambda: now)
    monkeypatch.setattr(
        memory_metrics_module,
        "get_memory_metric_store",
        lambda: store,
    )
    publisher(
        observation_type.model_validate(
            _compression_observation_payload(measurement_path="business")
        )
    )
    publisher(
        observation_type.model_validate(
            _compression_observation_payload(
                measurement_path="counterfactual",
                selected_unit_count=6,
                deduplicated_unit_count=5,
            )
        )
    )

    result = store.aggregate(window_minutes=60)
    items = [
        item
        for item in result["items"]
        if item["metric_code"] == "context_compression"
    ]

    assert len(items) == 2
    by_path = {
        item["dimensions"]["measurement_path"]: item for item in items
    }
    assert set(by_path) == {"business", "counterfactual"}
    assert by_path["business"]["values"]["selected_unit_count"] == 4
    assert by_path["counterfactual"]["values"]["selected_unit_count"] == 6
    assert by_path["business"]["values"]["deduplicated_unit_count"] == 3
    assert by_path["counterfactual"]["values"][
        "deduplicated_unit_count"
    ] == 5
    assert by_path["business"]["values"][
        "provider_input_tokens_when_available"
    ] == 3_060


def test_compression_observation_distinguishes_unavailable_usage_from_zero(
    monkeypatch,
):
    observation_type = getattr(
        memory_metrics_module,
        "CompressionObservation",
        None,
    )
    publisher = getattr(
        memory_metrics_module,
        "publish_compression_observation",
        None,
    )
    assert observation_type is not None, (
        "Task 9 RED: CompressionObservation is not implemented"
    )
    assert publisher is not None, (
        "Task 9 RED: publish_compression_observation is not implemented"
    )
    now = datetime.now(timezone.utc)
    store = InMemoryMemoryMetricStore(clock=lambda: now)
    monkeypatch.setattr(
        memory_metrics_module,
        "get_memory_metric_store",
        lambda: store,
    )
    publisher(
        observation_type.model_validate(
            _compression_observation_payload(
                measurement_path="business",
                provider_usage_available=False,
                provider_input_tokens_when_available=None,
                estimator_error_basis_points=0,
            )
        )
    )
    publisher(
        observation_type.model_validate(
            _compression_observation_payload(
                measurement_path="counterfactual",
                provider_usage_available=True,
                provider_input_tokens_when_available=0,
                estimator_error_basis_points=0,
            )
        )
    )

    items = {
        item["dimensions"]["provider_usage_available"]: item
        for item in store.aggregate(window_minutes=60)["items"]
        if item["metric_code"] == "context_compression"
    }
    assert items[False]["values"][
        "provider_input_tokens_when_available"
    ] is None
    assert items[True]["values"][
        "provider_input_tokens_when_available"
    ] == 0


def test_compression_observation_rejects_unbounded_and_private_metadata():
    observation_type = getattr(
        memory_metrics_module,
        "CompressionObservation",
        None,
    )
    assert observation_type is not None, (
        "Task 9 RED: CompressionObservation is not implemented"
    )
    invalid = (
        {"measurement_path": "merged"},
        {"source_token_bucket": "123-private-tokens"},
        {"latency_bucket": "PRIVATE_LATENCY"},
        {"business_utilization_basis_points": 100_001},
        {"estimator_error_basis_points": -1},
        {"selected_unit_count": -1},
        {"provider_usage_available": False},
        {"provider_input_tokens_when_available": None},
        {"prompt": "PRIVATE CANDIDATE ANSWER"},
        {"session_id": "PRIVATE SESSION"},
        {"owner_key_sha256": "f" * 64},
        {"raw_error": "PRIVATE PROVIDER ERROR"},
    )

    for changes in invalid:
        payload = _compression_observation_payload(**changes)
        if changes == {"provider_usage_available": False}:
            payload["provider_input_tokens_when_available"] = 3_060
        if changes == {"provider_input_tokens_when_available": None}:
            payload["provider_usage_available"] = True
        with pytest.raises(ValidationError):
            observation_type.model_validate(payload)
