"""PostgreSQL integration coverage."""

from __future__ import annotations
from threading import Event, Thread

import pytest

from app.runtime.config.compatibility import get_embedding_settings
from app.services.embedding_providers import DisabledEmbeddingProvider
from app.services.langgraph_runtime import PostgresCheckpointerRuntime
from app.services.postgres_connections import PooledPsycopg2ConnectionProvider
from app.services.postgres_runtime_control import PostgresRuntimeControlStore
from app.services.postgres_runtime_migrations import (
    PostgresMigrationConflict,
    migrate_postgres_runtime,
)
from app.services.postgres_schema_contract import LATEST_RUNTIME_MIGRATION
from tests.postgres_support import make_runtime_table_prefix, require_postgres_dsn


pytestmark = [pytest.mark.postgres_capacity, pytest.mark.pg_control]


@pytest.fixture
def isolated_schema():
    dsn = require_postgres_dsn()
    prefix = make_runtime_table_prefix("s48")
    vector = make_runtime_table_prefix("v48")
    embedding = DisabledEmbeddingProvider(model_name="disabled", dimension=3)
    result = migrate_postgres_runtime(
        dsn=dsn,
        table_prefix=prefix,
        pgvector_table=vector,
        embedding_provider=embedding,
    )
    yield dsn, prefix, vector, result


def make_pool(dsn, domain, maximum=2):
    provider = PooledPsycopg2ConnectionProvider(
        dsn,
        domain=domain,
        min_size=1,
        max_size=maximum,
        acquire_timeout=1,
        drain_timeout=2,
        application_name=f"interview_{domain}",
    )
    provider.open()
    return provider


def test_migration_is_idempotent_and_runtime_validation_is_read_only(isolated_schema):
    dsn, prefix, vector, first = isolated_schema
    assert first.applied is True
    second = migrate_postgres_runtime(
        dsn=dsn,
        table_prefix=prefix,
        pgvector_table=vector,
        embedding_provider=DisabledEmbeddingProvider(
            model_name="disabled", dimension=3
        ),
    )
    assert second.applied is False

    provider = make_pool(dsn, "business", maximum=2)
    try:
        store = PostgresRuntimeControlStore(
            dsn=dsn,
            connection_provider=provider,
            table_prefix=prefix,
            schema_mode="validate",
        )
        before = set(store.list_control_tables())
        PostgresRuntimeControlStore(
            dsn=dsn,
            connection_provider=provider,
            table_prefix=prefix,
            schema_mode="validate",
        )
        after = set(store.list_control_tables())
        assert before == after
    finally:
        provider.close()


def test_stage50_migration_installs_artifacts_and_v2_engine_constraint(
    isolated_schema,
):
    dsn, prefix, _, result = isolated_schema
    import psycopg2

    with psycopg2.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT to_regclass(%s), to_regclass(%s)",
                (
                    f"public.{prefix}_context_artifacts",
                    f"public.{prefix}_context_artifact_refs",
                ),
            )
            artifacts, refs = cursor.fetchone()
            cursor.execute(
                """
                SELECT pg_get_constraintdef(c.oid)
                FROM pg_constraint c
                WHERE c.conrelid = to_regclass(%s)
                  AND c.contype = 'c'
                  AND pg_get_constraintdef(c.oid) ILIKE '%%workflow_engine%%'
                """,
                (f"public.{prefix}_sessions",),
            )
            engine_constraints = [row[0] for row in cursor.fetchall()]
            cursor.execute(
                f'SELECT checksum, transaction_mode FROM "{prefix}_schema_migrations" '
                "WHERE migration_id = %s",
                (LATEST_RUNTIME_MIGRATION.migration_id,),
            )
            migration_row = cursor.fetchone()

    assert artifacts == f"{prefix}_context_artifacts"
    assert refs == f"{prefix}_context_artifact_refs"
    assert len(engine_constraints) == 1
    assert "langgraph-v2" in engine_constraints[0]
    assert result.migration_id == LATEST_RUNTIME_MIGRATION.migration_id
    assert migration_row == (
        LATEST_RUNTIME_MIGRATION.checksum,
        LATEST_RUNTIME_MIGRATION.transaction_mode,
    )


def test_stage50_applied_checksum_conflict_fails_closed(isolated_schema):
    dsn, prefix, vector, _ = isolated_schema
    import psycopg2
    from psycopg2 import sql

    with psycopg2.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                sql.SQL(
                    "UPDATE {migrations} SET checksum = %s "
                    "WHERE migration_id = %s"
                ).format(
                    migrations=sql.Identifier(
                        f"{prefix}_schema_migrations"
                    )
                ),
                ("0" * 64, LATEST_RUNTIME_MIGRATION.migration_id),
            )

    with pytest.raises(PostgresMigrationConflict, match="checksum diverged"):
        migrate_postgres_runtime(
            dsn=dsn,
            table_prefix=prefix,
            pgvector_table=vector,
            embedding_provider=DisabledEmbeddingProvider(
                model_name="disabled",
                dimension=3,
            ),
        )


def test_business_pool_reuses_connection_and_never_exceeds_max(isolated_schema):
    dsn, _, _, _ = isolated_schema
    provider = make_pool(dsn, "business", maximum=2)
    release = Event()
    entered = [Event(), Event()]
    backend_ids = []

    def hold(index):
        with provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_backend_pid()")
                backend_ids.append(cursor.fetchone()[0])
            entered[index].set()
            assert release.wait(2)

    threads = [Thread(target=hold, args=(index,)) for index in range(2)]
    try:
        for thread in threads:
            thread.start()
        assert all(event.wait(2) for event in entered)
        assert provider.snapshot().leased == 2
        assert provider.snapshot().peak_leased == 2
    finally:
        release.set()
        for thread in threads:
            thread.join(2)
        provider.close()
    assert len(set(backend_ids)) == 2


def test_telemetry_saturation_does_not_consume_business_capacity(isolated_schema):
    dsn, _, _, _ = isolated_schema
    business = make_pool(dsn, "business", maximum=1)
    telemetry = make_pool(dsn, "telemetry", maximum=1)
    try:
        with telemetry.connection():
            with business.connection() as connection:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT 1")
                    assert cursor.fetchone() == (1,)
        assert telemetry.snapshot().peak_leased == 1
        assert business.snapshot().peak_leased == 1
    finally:
        telemetry.close()
        business.close()


def test_installed_postgres_saver_accepts_pool_and_round_trips_missing_tuple(
    isolated_schema,
):
    dsn, _, _, _ = isolated_schema
    runtime = PostgresCheckpointerRuntime(dsn, min_size=1, max_size=2)
    try:
        saver = runtime.start()
        config = {"configurable": {"thread_id": "stage48-missing-thread"}}
        assert saver.get_tuple(config) is None
        assert runtime.pool.get_stats()["pool_max"] == 2
    finally:
        runtime.shutdown()
