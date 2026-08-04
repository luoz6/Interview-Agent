from datetime import datetime, timezone

import pytest

from app.services.postgres_principal_memory_control import (
    PostgresPrincipalMemoryControlStore,
)
from app.services.principal_memory_control import PrincipalMemoryControlConflict


@pytest.mark.pg_runtime
def test_postgres_controls_persist_across_store_restart(
    postgres_dsn,
    runtime_table_prefix,
):
    now = datetime(2026, 8, 4, tzinfo=timezone.utc)
    store = PostgresPrincipalMemoryControlStore(
        dsn=postgres_dsn,
        table_prefix=runtime_table_prefix,
        schema_mode="migrate",
    )
    try:
        global_control = store.set_global(
            deployment_id="single-tenant-local",
            principal_id="local-owner",
            enabled=False,
            updated_at=now,
        )
        session_control = store.set_session(
            deployment_id="single-tenant-local",
            principal_id="local-owner",
            session_id="session-a",
            enabled=False,
            updated_at=now,
        )

        restarted = PostgresPrincipalMemoryControlStore(
            dsn=postgres_dsn,
            table_prefix=runtime_table_prefix,
            schema_mode="validate",
        )

        assert restarted.get_global(
            deployment_id="single-tenant-local",
            principal_id="local-owner",
        ) == global_control
        assert restarted.get_session(
            deployment_id="single-tenant-local",
            principal_id="local-owner",
            session_id="session-a",
        ) == session_control
        assert restarted.count(
            deployment_id="single-tenant-local",
            principal_id="local-owner",
        ) == 2
        with pytest.raises(PrincipalMemoryControlConflict):
            restarted.set_global(
                deployment_id="single-tenant-local",
                principal_id="local-owner",
                enabled=True,
                updated_at=now,
                expected_version=0,
            )
        assert restarted.purge(
            deployment_id="single-tenant-local",
            principal_id="local-owner",
        ) == 2
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
