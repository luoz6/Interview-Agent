from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest

from app.services.postgres_principal_memory import PostgresPrincipalMemoryFactStore
from app.services.principal_memory_contracts import (
    CONSENT_POLICY_VERSION,
    TAXONOMY_VERSION,
    PrincipalMemoryFact,
    canonical_principal_fact,
    derive_principal_fact_id,
)
from tests.postgres_support import assert_safe_test_prefix
from tests.test_in_memory_principal_memory import NOW, make_fact


def make_active_language(value: str, digest: str):
    normalized = canonical_principal_fact({"interview_language": value})
    identity = {
        "deployment_id": "single-tenant-local",
        "principal_id": "local-owner",
        "fact_type": "declared_preference",
        "normalized_fact": normalized,
        "source_manifest_sha256": digest * 64,
        "source_excerpt_sha256": digest * 64,
        "consent_policy_version": CONSENT_POLICY_VERSION,
        "taxonomy_version": TAXONOMY_VERSION,
    }
    return PrincipalMemoryFact(
        fact_id=derive_principal_fact_id(**identity),
        **identity,
        confidence=1.0,
        authority="user_declared",
        status="active",
        source_session_id="local-user-declaration",
        user_confirmed=True,
        created_at=NOW,
        confirmed_at=NOW,
        expires_at=NOW + timedelta(days=180),
    )


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
        assert store.count_by_principal(
            deployment_id=fact.deployment_id,
            principal_id=fact.principal_id,
        ) == 0
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


@pytest.mark.pg_runtime
def test_postgres_exclusive_declarations_are_atomic(
    postgres_dsn,
    runtime_table_prefix,
):
    store = PostgresPrincipalMemoryFactStore(
        dsn=postgres_dsn,
        table_prefix=runtime_table_prefix,
        schema_mode="migrate",
    )
    facts = [
        make_active_language("zh_hans", "c"),
        make_active_language("en", "d"),
        make_active_language("mixed", "e"),
    ] * 4
    try:
        with ThreadPoolExecutor(max_workers=6) as executor:
            list(
                executor.map(
                    lambda fact: store.declare_active(
                        fact,
                        exclusive_key="interview_language",
                        now=NOW,
                    ),
                    facts,
                )
            )
        stored = store.list_by_principal(
            deployment_id="single-tenant-local",
            principal_id="local-owner",
            limit=100,
            include_terminal=True,
        )
        assert [fact.status for fact in stored].count("active") == 1
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


@pytest.mark.pg_runtime
def test_postgres_confirmation_competes_atomically_and_retention_is_bounded(
    postgres_dsn,
    runtime_table_prefix,
):
    store = PostgresPrincipalMemoryFactStore(
        dsn=postgres_dsn,
        table_prefix=runtime_table_prefix,
        schema_mode="migrate",
    )
    proposal = make_active_language("en", "f").model_copy(
        update={
            "authority": "model_proposed",
            "status": "proposed",
            "user_confirmed": False,
            "confirmed_at": None,
            "expires_at": None,
        }
    )
    direct = make_active_language("mixed", "0")
    try:
        store.create_proposal(proposal)
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(
                    store.activate_proposal,
                    deployment_id=proposal.deployment_id,
                    principal_id=proposal.principal_id,
                    fact_id=proposal.fact_id,
                    expected_version=1,
                    exclusive_key="interview_language",
                    now=NOW,
                    expires_at=NOW + timedelta(days=180),
                ),
                executor.submit(
                    store.declare_active,
                    direct,
                    exclusive_key="interview_language",
                    now=NOW,
                ),
            ]
            for future in futures:
                future.result()
        stored = store.list_by_principal(
            deployment_id="single-tenant-local",
            principal_id="local-owner",
            limit=100,
            include_terminal=True,
        )
        assert [fact.status for fact in stored].count("active") == 1

        old_proposal = make_fact(value="kafka").model_copy(
            update={"created_at": NOW - timedelta(days=7)}
        )
        store.create_proposal(old_proposal)
        assert store.expire_batch(
            now=NOW,
            proposal_created_before=NOW - timedelta(days=7),
            limit=1,
        ) == 1
        assert store.get(
            deployment_id=old_proposal.deployment_id,
            principal_id=old_proposal.principal_id,
            fact_id=old_proposal.fact_id,
        ).status == "expired"
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
