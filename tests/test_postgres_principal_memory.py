from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest

from app.services.postgres_principal_memory import PostgresPrincipalMemoryFactStore
from tests.postgres_support import assert_safe_test_prefix
from tests.test_in_memory_principal_memory import NOW, make_fact


@pytest.mark.pg_runtime
def test_postgres_principal_fact_store_dedup_cas_isolation_and_purge(
    postgres_dsn, runtime_table_prefix
):
    prefix = runtime_table_prefix
    assert_safe_test_prefix(prefix)
    store = PostgresPrincipalMemoryFactStore(
        dsn=postgres_dsn, table_prefix=prefix, schema_mode="migrate"
    )
    fact = make_fact()
    try:
        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(store.create_proposal, [fact] * 8))
        assert {item.fact_id for item in results} == {fact.fact_id}
        active = store.transition(
            deployment_id=fact.deployment_id,
            principal_id=fact.principal_id,
            fact_id=fact.fact_id,
            expected_version=1,
            target_status="active",
            now=NOW,
            expires_at=NOW + timedelta(days=365),
        )
        assert active.status == "active"
        assert len(store.list_shadow_eligible(
            deployment_id=fact.deployment_id,
            principal_id=fact.principal_id,
            now=NOW,
            limit=6,
        )) == 1
        assert store.list_by_principal(
            deployment_id=fact.deployment_id,
            principal_id="principal-other",
            limit=6,
        ) == []
        import psycopg2
        from psycopg2 import sql

        with psycopg2.connect(postgres_dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        "INSERT INTO {effects} (effect_id,deployment_id,"
                        "principal_id,source_session_id,status,created_at,updated_at) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s)"
                    ).format(effects=sql.Identifier(store.effects_table)),
                    (
                        "principal-effect-restore-drill",
                        fact.deployment_id,
                        fact.principal_id,
                        fact.source_session_id,
                        "queued",
                        NOW,
                        NOW,
                    ),
                )
        assert store.purge_by_session(fact.source_session_id) == 2
        with psycopg2.connect(postgres_dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL("SELECT COUNT(*) FROM {effects}").format(
                        effects=sql.Identifier(store.effects_table)
                    )
                )
                assert cursor.fetchone()[0] == 0
    finally:
        import psycopg2
        from psycopg2 import sql
        with psycopg2.connect(postgres_dsn) as connection:
            with connection.cursor() as cursor:
                for table in (store.effects_table, store.table):
                    cursor.execute(
                        sql.SQL("DROP TABLE IF EXISTS {table} CASCADE").format(
                            table=sql.Identifier(table)
                        )
                    )
