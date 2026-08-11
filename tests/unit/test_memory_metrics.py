from datetime import datetime, timezone

import pytest

from app.services.memory_metrics import (
    InMemoryMemoryMetricStore,
    MemoryMetricDimensions,
    MemoryMetricEvent,
    MemoryMetricValues,
    ResilientMemoryMetricStore,
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
