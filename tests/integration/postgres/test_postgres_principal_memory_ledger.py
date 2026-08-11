"""PostgreSQL integration coverage."""

from __future__ import annotations
from datetime import datetime, timezone

import pytest

from app.services.postgres_connections import PostgresSchemaNotReady
from app.services.postgres_principal_memory_ledger import (
    PostgresPrincipalMemoryLedgerWatermarkStore,
)
from app.services.principal_memory_ledger import GENESIS_HEAD_SHA256
from tests.postgres_support import assert_safe_test_prefix


NOW = datetime(2026, 8, 4, 18, tzinfo=timezone.utc)


def _drop(postgres_dsn: str, prefix: str) -> None:
    import psycopg2
    from psycopg2 import sql

    assert_safe_test_prefix(prefix)
    with psycopg2.connect(postgres_dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                sql.SQL("DROP TABLE IF EXISTS {} CASCADE").format(
                    sql.Identifier(f"{prefix}_principal_memory_ledger_watermark")
                )
            )


@pytest.mark.pg_runtime
def test_watermark_genesis_persists_and_advances_with_cas(
    postgres_dsn, runtime_table_prefix
):
    prefix = runtime_table_prefix
    first_head = "1" * 64
    try:
        store = PostgresPrincipalMemoryLedgerWatermarkStore(
            dsn=postgres_dsn, table_prefix=prefix, schema_mode="migrate"
        )
        assert store.get().last_applied_ledger_event_count == 0
        assert store.get().last_applied_ledger_head_sha256 == GENESIS_HEAD_SHA256

        reopened = PostgresPrincipalMemoryLedgerWatermarkStore(
            dsn=postgres_dsn, table_prefix=prefix, schema_mode="validate"
        )
        advanced = reopened.advance(
            expected_event_count=0,
            expected_head_sha256=GENESIS_HEAD_SHA256,
            new_event_count=1,
            new_head_sha256=first_head,
            applied_at=NOW,
        )
        assert advanced.last_applied_ledger_event_count == 1
        assert advanced.last_applied_ledger_head_sha256 == first_head
        assert advanced.last_applied_at == NOW

        with pytest.raises(RuntimeError, match="watermark conflict"):
            store.advance(
                expected_event_count=0,
                expected_head_sha256=GENESIS_HEAD_SHA256,
                new_event_count=1,
                new_head_sha256="2" * 64,
                applied_at=NOW,
            )
    finally:
        _drop(postgres_dsn, prefix)


@pytest.mark.pg_runtime
def test_watermark_rejects_invalid_or_backwards_advances(
    postgres_dsn, runtime_table_prefix
):
    prefix = runtime_table_prefix
    try:
        store = PostgresPrincipalMemoryLedgerWatermarkStore(
            dsn=postgres_dsn, table_prefix=prefix, schema_mode="migrate"
        )
        with pytest.raises(ValueError, match="exactly one event"):
            store.advance(
                expected_event_count=1,
                expected_head_sha256="1" * 64,
                new_event_count=0,
                new_head_sha256=GENESIS_HEAD_SHA256,
                applied_at=NOW,
            )
        with pytest.raises(ValueError, match="SHA-256"):
            store.advance(
                expected_event_count=0,
                expected_head_sha256="invalid",
                new_event_count=1,
                new_head_sha256="2" * 64,
                applied_at=NOW,
            )
        with pytest.raises(ValueError, match="timezone-aware"):
            store.advance(
                expected_event_count=0,
                expected_head_sha256=GENESIS_HEAD_SHA256,
                new_event_count=1,
                new_head_sha256="2" * 64,
                applied_at=NOW.replace(tzinfo=None),
            )
        with pytest.raises(ValueError, match="exactly one event"):
            store.advance(
                expected_event_count=0,
                expected_head_sha256=GENESIS_HEAD_SHA256,
                new_event_count=2,
                new_head_sha256="2" * 64,
                applied_at=NOW,
            )
    finally:
        _drop(postgres_dsn, prefix)


@pytest.mark.pg_runtime
def test_watermark_validate_mode_rejects_missing_relation(
    postgres_dsn, runtime_table_prefix
):
    prefix = runtime_table_prefix
    _drop(postgres_dsn, prefix)
    with pytest.raises(PostgresSchemaNotReady):
        PostgresPrincipalMemoryLedgerWatermarkStore(
            dsn=postgres_dsn, table_prefix=prefix, schema_mode="validate"
        )
