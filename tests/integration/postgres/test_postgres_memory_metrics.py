"""PostgreSQL integration coverage."""

from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from app.services.memory_metrics import (
    MemoryMetricDimensions,
    MemoryMetricEvent,
    MemoryMetricValues,
)
from app.services.postgres_memory_metrics import (
    PostgresMemoryMetricStore,
    canonical_dimensions,
)
from app.services.postgres_schema_contract import required_columns_for_relation
from tests.postgres_support import assert_safe_test_prefix


def test_canonical_dimensions_are_stable_and_do_not_accept_subject_fields():
    left = {"operation": "followup", "route": "deterministic"}
    right = {"route": "deterministic", "operation": "followup"}

    assert canonical_dimensions(left) == canonical_dimensions(right)
    columns = required_columns_for_relation("test_memory_metric_buckets")
    assert "dimensions_sha256" in columns
    assert not {
        "session_id",
        "principal_id",
        "fact_id",
        "question_id",
        "prompt",
        "answer",
        "summary",
        "excerpt",
    } & columns


@pytest.mark.pg_runtime
def test_postgres_metric_bucket_atomic_upsert_rollup_and_retention(
    postgres_dsn,
    runtime_table_prefix,
):
    prefix = runtime_table_prefix
    assert_safe_test_prefix(prefix)
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    store = PostgresMemoryMetricStore(
        dsn=postgres_dsn,
        table_prefix=prefix,
        schema_mode="migrate",
        clock=lambda: now,
        minimum_language_samples=5,
    )
    event = MemoryMetricEvent(
        metric_code="provider_usage",
        dimensions=MemoryMetricDimensions(
            operation="provider",
            language_bucket="zh_hans",
        ),
        values=MemoryMetricValues(provider_input_tokens=10),
        observed_at=now - timedelta(minutes=65),
    )
    try:
        with ThreadPoolExecutor(max_workers=8) as executor:
            list(executor.map(store.publish, [event] * 16))

        result = store.aggregate(window_minutes=1440)
        assert result["store_kind"] == "postgres_aggregate"
        assert result["data_complete"] is True
        assert result["items"][0]["values"]["event_count"] == 16
        assert result["items"][0]["values"]["provider_input_tokens"] == 160
        assert result["items"][0]["sample_status"] == "sufficient"

        assert store.rollup(batch_size=100) >= 1
        assert store.rollup(batch_size=100) >= 1
        deleted = store.cleanup(
            minute_retention_days=1,
            hour_retention_days=1,
            batch_size=100,
        )
        assert set(deleted) == {"minute_deleted", "hour_deleted"}
    finally:
        import psycopg2
        from psycopg2 import sql

        with psycopg2.connect(postgres_dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL("DROP TABLE IF EXISTS {table} CASCADE").format(
                        table=sql.Identifier(store.table)
                    )
                )
