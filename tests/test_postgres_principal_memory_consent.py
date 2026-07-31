from datetime import datetime, timezone

import pytest

from app.services.postgres_principal_memory_consent import (
    PostgresPrincipalMemoryConsentStore,
)
from app.services.principal_memory_consent import PrincipalMemoryConsent


@pytest.mark.pg_runtime
def test_postgres_consent_grant_revoke_and_purge(
    postgres_dsn, runtime_table_prefix
):
    store = PostgresPrincipalMemoryConsentStore(
        dsn=postgres_dsn,
        table_prefix=runtime_table_prefix,
        schema_mode="migrate",
    )
    now = datetime(2026, 7, 30, tzinfo=timezone.utc)
    try:
        granted = store.grant(
            PrincipalMemoryConsent(
                deployment_id="single-tenant-local",
                principal_id="principal-pg",
                policy_version="principal-memory-consent-v1",
                allowed_purposes=["proposal_write", "fact_storage", "read_shadow"],
                granted_at=now,
            )
        )
        assert granted.version == 1
        assert store.get_current(
            deployment_id="single-tenant-local", principal_id="principal-pg"
        ) == granted
        revoked = store.revoke(
            deployment_id="single-tenant-local",
            principal_id="principal-pg",
            revoked_at=now,
        )
        assert revoked.revoked_at == now
        assert store.purge(
            deployment_id="single-tenant-local", principal_id="principal-pg"
        ) == 1
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
