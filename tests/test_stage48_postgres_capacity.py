from __future__ import annotations

from contextlib import contextmanager
from threading import Event, Thread

import pytest

from app.services.config import get_embedding_settings
from app.services.embedding_providers import DisabledEmbeddingProvider
from app.services.langgraph_runtime import PostgresCheckpointerRuntime
from app.services.postgres_connections import PooledPsycopg2ConnectionProvider
from app.services.postgres_runtime_control import PostgresRuntimeControlStore
from app.services.postgres_runtime_migrations import migrate_postgres_runtime
from tests.postgres_support import make_runtime_table_prefix, require_postgres_dsn


pytestmark = [pytest.mark.postgres_capacity, pytest.mark.pg_control]


@pytest.fixture
def isolated_schema():
    dsn = require_postgres_dsn()
    prefix = make_runtime_table_prefix("s48")
    vector = make_runtime_table_prefix("v48")
    embedding = DisabledEmbeddingProvider(model_name="disabled", dimension=3)
    try:
        result = migrate_postgres_runtime(
            dsn=dsn,
            table_prefix=prefix,
            pgvector_table=vector,
            embedding_provider=embedding,
        )
        yield dsn, prefix, vector, result
    finally:
        _drop_isolated_relations(dsn, prefix, vector)


def _drop_isolated_relations(dsn, prefix, vector):
    import psycopg2
    from psycopg2 import sql

    with psycopg2.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = current_schema()
                  AND (table_name LIKE %s OR table_name LIKE %s)
                """,
                (prefix + "_%", vector + "_%"),
            )
            names = [row[0] for row in cursor.fetchall()]
            for name in names:
                cursor.execute(
                    sql.SQL("DROP TABLE IF EXISTS {} CASCADE").format(
                        sql.Identifier(name)
                    )
                )


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
