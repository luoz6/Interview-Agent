from datetime import datetime, timezone

from app.services.postgres_schema_contract import (
    LATEST_RUNTIME_MIGRATION,
    RUNTIME_MIGRATIONS,
    required_columns_for_relation,
    required_index_tokens_for_relation,
)
from app.services.postgres_session_deletion import (
    PostgresSessionDeletionJobStore,
)


def test_deletion_store_table_name_follows_runtime_prefix(monkeypatch):
    monkeypatch.setattr(
        PostgresSessionDeletionJobStore,
        "_ensure_schema",
        lambda self: None,
    )
    provider = type("Provider", (), {})()

    store = PostgresSessionDeletionJobStore(
        connection_provider=provider,
        table_prefix="memory_test",
        schema_mode="migrate",
    )

    assert store.table == "memory_test_session_deletion_jobs"
    assert store._connection_provider is provider


def test_deletion_row_mapping_exposes_prefixed_safe_job_identifier():
    now = datetime(2026, 7, 30, tzinfo=timezone.utc)

    job = PostgresSessionDeletionJobStore._from_row(
        (
            "00000000-0000-0000-0000-000000000001",
            "session-private",
            "completed",
            1,
            None,
            None,
            None,
            1,
            None,
            {"business_sessions": 1},
            now,
            now,
            now,
        )
    )

    assert job.job_id == "delete-00000000-0000-0000-0000-000000000001"
    assert job.safe_counts == {"business_sessions": 1}


def test_latest_migration_contract_requires_deletion_lease_and_indexes():
    assert any(
        migration.migration_id == "principal_memory_v1"
        for migration in RUNTIME_MIGRATIONS
    )
    assert (
        LATEST_RUNTIME_MIGRATION.migration_id
        == "frontend_product_experience_v15"
    )
    columns = required_columns_for_relation(
        "memory_test_session_deletion_jobs"
    )
    indexes = required_index_tokens_for_relation(
        "memory_test_session_deletion_jobs"
    )

    assert {"lease_token", "fencing_version", "safe_counts"} <= columns
    assert any("queued" in tokens for tokens in indexes)
    assert any("failed" in tokens for tokens in indexes)
    assert any("lease_expires_at" in tokens for tokens in indexes)

    tombstone_columns = required_columns_for_relation(
        "memory_test_session_deletion_tombstones"
    )
    tombstone_indexes = required_index_tokens_for_relation(
        "memory_test_session_deletion_tombstones"
    )
    assert {
        "session_id",
        "deletion_job_id",
        "integrity_sha256",
        "replay_status",
    } <= tombstone_columns
    assert any("replay_status" in tokens for tokens in tombstone_indexes)
