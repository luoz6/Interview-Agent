from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest

from app.services.postgres_principal_memory import PostgresPrincipalMemoryFactStore
from app.services.in_memory_principal_memory import PrincipalMemoryConflict
from app.services.principal_memory_contracts import (
    CONSENT_POLICY_VERSION,
    TAXONOMY_VERSION,
    PrincipalMemoryFact,
    canonical_principal_fact,
    derive_principal_fact_id,
)
from app.services.postgres_identifiers import runtime_schema_identifier
from app.services.postgres_connections import PostgresSchemaNotReady
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
        active = store.activate_proposal(
            deployment_id=fact.deployment_id,
            principal_id=fact.principal_id,
            fact_id=fact.fact_id,
            expected_version=1,
            exclusive_key=None,
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


@pytest.mark.pg_runtime
def test_postgres_same_value_proposal_confirmations_leave_one_active_fact(
    postgres_dsn,
    runtime_table_prefix,
):
    store = PostgresPrincipalMemoryFactStore(
        dsn=postgres_dsn,
        table_prefix=runtime_table_prefix,
        schema_mode="migrate",
    )
    proposals = [
        make_active_language("en", digest).model_copy(
            update={
                "authority": "model_proposed",
                "status": "proposed",
                "user_confirmed": False,
                "confirmed_at": None,
                "expires_at": None,
                "source_session_id": f"session-{digest}",
            }
        )
        for digest in ("1", "2")
    ]
    try:
        for proposal in proposals:
            store.create_proposal(proposal)
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(
                executor.map(
                    lambda proposal: store.activate_proposal(
                        deployment_id=proposal.deployment_id,
                        principal_id=proposal.principal_id,
                        fact_id=proposal.fact_id,
                        expected_version=1,
                        exclusive_key="interview_language",
                        now=NOW,
                        expires_at=NOW + timedelta(days=180),
                    ),
                    proposals,
                )
            )
        assert all(result is not None for result in results)
        stored = store.list_by_principal(
            deployment_id="single-tenant-local",
            principal_id="local-owner",
            limit=100,
            include_terminal=True,
        )
        assert [fact.status for fact in stored].count("active") == 1
        assert [fact.status for fact in stored].count("superseded") == 1
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
def test_postgres_concurrent_corrections_validate_exact_predecessor(
    postgres_dsn,
    runtime_table_prefix,
):
    store = PostgresPrincipalMemoryFactStore(
        dsn=postgres_dsn,
        table_prefix=runtime_table_prefix,
        schema_mode="migrate",
    )
    predecessor = make_active_language("zh_hans", "3")
    corrections = [
        make_active_language("en", "4"),
        make_active_language("mixed", "5"),
    ]
    try:
        store.declare_active(
            predecessor,
            exclusive_key="interview_language",
            now=NOW,
        )
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(
                    store.declare_active,
                    correction,
                    exclusive_key="interview_language",
                    now=NOW,
                    expected_predecessor_fact_id=predecessor.fact_id,
                    expected_predecessor_version=predecessor.version,
                )
                for correction in corrections
            ]
            outcomes = []
            for future in futures:
                try:
                    outcomes.append(future.result().status)
                except PrincipalMemoryConflict:
                    outcomes.append("conflict")
        assert sorted(outcomes) == ["active", "conflict"]
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
def test_store_rejects_forged_exclusive_scope_key(
    postgres_dsn,
    runtime_table_prefix,
):
    store = PostgresPrincipalMemoryFactStore(
        dsn=postgres_dsn,
        table_prefix=runtime_table_prefix,
        schema_mode="migrate",
    )
    fact = make_active_language("en", "6")
    try:
        for forged in (None, "target_role_family"):
            with pytest.raises(ValueError, match="database-owned taxonomy"):
                store.declare_active(
                    fact,
                    exclusive_key=forged,
                    now=NOW,
                )
        assert store.list_by_principal(
            deployment_id=fact.deployment_id,
            principal_id=fact.principal_id,
            limit=10,
            include_terminal=True,
        ) == []
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
def test_database_rejects_direct_sql_exclusive_scope_bypass(
    postgres_dsn,
    runtime_table_prefix,
):
    import psycopg2
    from psycopg2 import sql

    store = PostgresPrincipalMemoryFactStore(
        dsn=postgres_dsn,
        table_prefix=runtime_table_prefix,
        schema_mode="migrate",
    )
    first = make_active_language("en", "7")
    second = make_active_language("mixed", "8")
    try:
        forged_params = list(store._params(second))
        forged_params[-1] = None
        connection = psycopg2.connect(postgres_dsn)
        connection.autocommit = True
        try:
            with connection.cursor() as cursor:
                with pytest.raises(psycopg2.errors.CheckViolation):
                    cursor.execute(
                        sql.SQL(
                            "INSERT INTO {table} ({columns}) VALUES ({values})"
                        ).format(
                            table=sql.Identifier(store.table),
                            columns=sql.SQL(store._insert_columns()),
                            values=sql.SQL(",").join(
                                sql.Placeholder() for _ in range(26)
                            ),
                        ),
                        forged_params,
                    )
        finally:
            connection.close()
        store.declare_active(
            first,
            exclusive_key="interview_language",
            now=NOW,
        )
        connection = psycopg2.connect(postgres_dsn)
        connection.autocommit = True
        try:
            with connection.cursor() as cursor:
                with pytest.raises(psycopg2.errors.UniqueViolation):
                    cursor.execute(
                        sql.SQL(
                            "INSERT INTO {table} ({columns}) VALUES ({values})"
                        ).format(
                            table=sql.Identifier(store.table),
                            columns=sql.SQL(store._insert_columns()),
                            values=sql.SQL(",").join(
                                sql.Placeholder() for _ in range(26)
                            ),
                        ),
                        store._params(second),
                    )
        finally:
            connection.close()
        stored = store.list_by_principal(
            deployment_id=first.deployment_id,
            principal_id=first.principal_id,
            limit=10,
            include_terminal=True,
        )
        assert [item.status for item in stored].count("active") == 1
    finally:
        with psycopg2.connect(postgres_dsn) as connection:
            with connection.cursor() as cursor:
                for table in (store.effects_table, store.table):
                    cursor.execute(
                        sql.SQL("DROP TABLE IF EXISTS {table} CASCADE").format(
                            table=sql.Identifier(table)
                        )
                    )


@pytest.mark.pg_runtime
def test_concurrent_direct_writers_get_one_database_winner(
    postgres_dsn,
    runtime_table_prefix,
):
    import psycopg2
    from psycopg2 import sql

    store = PostgresPrincipalMemoryFactStore(
        dsn=postgres_dsn,
        table_prefix=runtime_table_prefix,
        schema_mode="migrate",
    )
    facts = [
        make_active_language("en", "f"),
        make_active_language("mixed", "0"),
    ]

    def insert(fact):
        connection = psycopg2.connect(postgres_dsn)
        connection.autocommit = True
        try:
            with connection.cursor() as cursor:
                try:
                    cursor.execute(
                        sql.SQL(
                            "INSERT INTO {table} ({columns}) VALUES ({values})"
                        ).format(
                            table=sql.Identifier(store.table),
                            columns=sql.SQL(store._insert_columns()),
                            values=sql.SQL(",").join(
                                sql.Placeholder() for _ in range(26)
                            ),
                        ),
                        store._params(fact),
                    )
                    return "inserted"
                except psycopg2.errors.UniqueViolation:
                    return "unique_violation"
        finally:
            connection.close()

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(insert, facts))
        assert sorted(outcomes) == ["inserted", "unique_violation"]
    finally:
        with psycopg2.connect(postgres_dsn) as connection:
            with connection.cursor() as cursor:
                for table in (store.effects_table, store.table):
                    cursor.execute(
                        sql.SQL("DROP TABLE IF EXISTS {table} CASCADE").format(
                            table=sql.Identifier(table)
                        )
                    )


@pytest.mark.pg_runtime
def test_terminal_statuses_release_the_exclusive_database_slot(
    postgres_dsn,
    runtime_table_prefix,
):
    import psycopg2
    from psycopg2 import sql

    store = PostgresPrincipalMemoryFactStore(
        dsn=postgres_dsn,
        table_prefix=runtime_table_prefix,
        schema_mode="migrate",
    )
    facts = [
        make_active_language("en", "9"),
        make_active_language("mixed", "a"),
        make_active_language("zh_hans", "b"),
        make_active_language("en", "c"),
    ]
    try:
        for index, terminal in enumerate(("revoked", "expired", "deleted")):
            active = store.declare_active(
                facts[index],
                exclusive_key="interview_language",
                now=NOW,
            )
            store.transition(
                deployment_id=active.deployment_id,
                principal_id=active.principal_id,
                fact_id=active.fact_id,
                expected_version=active.version,
                target_status=terminal,
                now=NOW,
            )
        final = store.declare_active(
            facts[-1],
            exclusive_key="interview_language",
            now=NOW,
        )
        assert final.status == "active"
    finally:
        with psycopg2.connect(postgres_dsn) as connection:
            with connection.cursor() as cursor:
                for table in (store.effects_table, store.table):
                    cursor.execute(
                        sql.SQL("DROP TABLE IF EXISTS {table} CASCADE").format(
                            table=sql.Identifier(table)
                        )
                    )


@pytest.mark.pg_runtime
def test_failed_replacement_insert_rolls_back_predecessor_supersede(
    postgres_dsn,
    runtime_table_prefix,
):
    import psycopg2
    from psycopg2 import sql

    store = PostgresPrincipalMemoryFactStore(
        dsn=postgres_dsn,
        table_prefix=runtime_table_prefix,
        schema_mode="migrate",
    )
    predecessor = make_active_language("en", "d")
    replacement = make_active_language("mixed", "e")
    function_name = runtime_schema_identifier(
        runtime_table_prefix,
        "reject_principal_memory_replacement",
    )
    trigger_name = runtime_schema_identifier(
        runtime_table_prefix,
        "reject_principal_memory_replacement_trigger",
    )
    try:
        store.declare_active(
            predecessor,
            exclusive_key="interview_language",
            now=NOW,
        )
        with psycopg2.connect(postgres_dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        "CREATE FUNCTION {function}() RETURNS trigger "
                        "LANGUAGE plpgsql AS $$ BEGIN "
                        "IF NEW.fact_id={replacement} THEN "
                        "RAISE EXCEPTION 'injected replacement failure'; "
                        "END IF; RETURN NEW; END $$"
                    ).format(
                        function=sql.Identifier(function_name),
                        replacement=sql.Literal(replacement.fact_id),
                    )
                )
                cursor.execute(
                    sql.SQL(
                        "CREATE TRIGGER {trigger} BEFORE INSERT ON {table} "
                        "FOR EACH ROW EXECUTE FUNCTION {function}()"
                    ).format(
                        trigger=sql.Identifier(trigger_name),
                        table=sql.Identifier(store.table),
                        function=sql.Identifier(function_name),
                    )
                )
        with pytest.raises(psycopg2.errors.RaiseException):
            store.declare_active(
                replacement,
                exclusive_key="interview_language",
                now=NOW,
            )
        current = store.get(
            deployment_id=predecessor.deployment_id,
            principal_id=predecessor.principal_id,
            fact_id=predecessor.fact_id,
        )
        assert current.status == "active"
        assert current.version == predecessor.version
    finally:
        with psycopg2.connect(postgres_dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL("DROP TABLE IF EXISTS {table} CASCADE").format(
                        table=sql.Identifier(store.effects_table)
                    )
                )
                cursor.execute(
                    sql.SQL("DROP TABLE IF EXISTS {table} CASCADE").format(
                        table=sql.Identifier(store.table)
                    )
                )
                cursor.execute(
                    sql.SQL("DROP FUNCTION IF EXISTS {function}()").format(
                        function=sql.Identifier(function_name)
                    )
                )


@pytest.mark.pg_runtime
def test_schema_validation_rejects_missing_taxonomy_scope_check(
    postgres_dsn,
    runtime_table_prefix,
):
    import psycopg2
    from psycopg2 import sql

    store = PostgresPrincipalMemoryFactStore(
        dsn=postgres_dsn,
        table_prefix=runtime_table_prefix,
        schema_mode="migrate",
    )
    try:
        with psycopg2.connect(postgres_dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT rule.conname,pg_get_constraintdef(rule.oid) "
                    "FROM pg_constraint AS rule "
                    "JOIN pg_class AS relation ON relation.oid=rule.conrelid "
                    "WHERE relation.relname=%s AND rule.contype='c'",
                    (store.table,),
                )
                taxonomy_checks = [
                    name
                    for name, definition in cursor.fetchall()
                    if "taxonomy_key" in definition
                    and "exclusive_scope_key" in definition
                ]
                assert taxonomy_checks
                for name in taxonomy_checks:
                    cursor.execute(
                        sql.SQL(
                            "ALTER TABLE {table} DROP CONSTRAINT {constraint}"
                        ).format(
                            table=sql.Identifier(store.table),
                            constraint=sql.Identifier(name),
                        )
                    )
        with pytest.raises(PostgresSchemaNotReady, match="checks"):
            PostgresPrincipalMemoryFactStore(
                dsn=postgres_dsn,
                table_prefix=runtime_table_prefix,
                schema_mode="validate",
            )
    finally:
        with psycopg2.connect(postgres_dsn) as connection:
            with connection.cursor() as cursor:
                for table in (store.effects_table, store.table):
                    cursor.execute(
                        sql.SQL("DROP TABLE IF EXISTS {table} CASCADE").format(
                            table=sql.Identifier(table)
                        )
                    )
