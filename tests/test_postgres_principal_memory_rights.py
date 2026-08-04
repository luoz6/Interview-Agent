from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from app.services.postgres_principal_memory import PostgresPrincipalMemoryFactStore
from app.services.postgres_principal_memory_consent import (
    PostgresPrincipalMemoryConsentStore,
)
from app.services.postgres_principal_memory_control import (
    PostgresPrincipalMemoryControlStore,
)
from app.services.postgres_principal_memory_rights import (
    PostgresPrincipalMemoryDeletionTombstoneStore,
    PostgresPrincipalMemoryExportStore,
    PostgresPrincipalMemorySafeRefStore,
)
from app.services.postgres_connections import PostgresSchemaNotReady
from app.services.principal_identity import ExplicitPrincipalIdentityResolver
from app.services.principal_memory_consent import PrincipalMemoryConsent
from app.services.principal_memory_control import PrincipalMemoryControlService
from app.services.principal_memory_deletion import PrincipalMemoryDeletionService
from app.services.principal_memory_rights import (
    PrincipalMemoryExportRecord,
    PrincipalMemoryExportService,
)
from tests.postgres_support import assert_safe_test_prefix
from tests.test_postgres_principal_memory import make_active_language


NOW = datetime(2026, 8, 4, 12, tzinfo=timezone.utc)


class SafeLifecycle:
    def list_safe(self, *, limit):
        assert limit == 100
        return [
            {
                "fact_type": "declared_preference",
                "normalized_value": {"interview_language": "mixed"},
                "status": "active",
                "version": 1,
                "created_at": NOW.isoformat(),
                "confirmed_at": NOW.isoformat(),
                "expires_at": None,
                "revocable": True,
            }
        ]


def stores(postgres_dsn, prefix, *, schema_mode="migrate", clock=None):
    return {
        "facts": PostgresPrincipalMemoryFactStore(
            dsn=postgres_dsn, table_prefix=prefix, schema_mode=schema_mode
        ),
        "consents": PostgresPrincipalMemoryConsentStore(
            dsn=postgres_dsn, table_prefix=prefix, schema_mode=schema_mode
        ),
        "controls": PostgresPrincipalMemoryControlStore(
            dsn=postgres_dsn, table_prefix=prefix, schema_mode=schema_mode
        ),
        "exports": PostgresPrincipalMemoryExportStore(
            dsn=postgres_dsn, table_prefix=prefix, schema_mode=schema_mode
        ),
        "tombstones": PostgresPrincipalMemoryDeletionTombstoneStore(
            dsn=postgres_dsn,
            table_prefix=prefix,
            schema_mode=schema_mode,
            clock=clock or (lambda: NOW),
        ),
        "refs": PostgresPrincipalMemorySafeRefStore(
            dsn=postgres_dsn,
            table_prefix=prefix,
            schema_mode=schema_mode,
            clock=clock or (lambda: NOW),
            ref_factory=lambda: "pm-ref-" + "a" * 32,
        ),
    }


def cleanup(postgres_dsn, values):
    import psycopg2
    from psycopg2 import sql

    tables = {
        values["facts"].effects_table,
        values["facts"].table,
        values["consents"].table,
        values["controls"].table,
        values["exports"].table,
        values["tombstones"].table,
        values["refs"].table,
    }
    with psycopg2.connect(postgres_dsn) as connection:
        with connection.cursor() as cursor:
            for table in tables:
                cursor.execute(
                    sql.SQL("DROP TABLE IF EXISTS {table} CASCADE").format(
                        table=sql.Identifier(table)
                    )
                )


@pytest.mark.pg_runtime
def test_postgres_export_is_durable_scoped_and_expires(
    postgres_dsn, runtime_table_prefix
):
    prefix = runtime_table_prefix
    assert_safe_test_prefix(prefix)
    values = stores(postgres_dsn, prefix)
    record = PrincipalMemoryExportRecord(
        export_ref="pm-export-" + "b" * 32,
        deployment_id="single-tenant-local",
        principal_id="local-owner",
        payload={"schema_version": "principal-memory-safe-export-v1", "facts": []},
        created_at=NOW,
        expires_at=NOW + timedelta(hours=24),
    )
    try:
        assert values["exports"].put(record) == record
        restarted = PostgresPrincipalMemoryExportStore(
            dsn=postgres_dsn, table_prefix=prefix, schema_mode="validate"
        )
        assert restarted.get(record.export_ref, now=NOW) == record
        assert restarted.get(record.export_ref, now=record.expires_at) is None
        assert restarted.count(
            deployment_id="single-tenant-local", principal_id="other-owner"
        ) == 0
        assert restarted.purge(
            deployment_id="single-tenant-local", principal_id="local-owner"
        ) == 1
    finally:
        cleanup(postgres_dsn, values)


@pytest.mark.pg_runtime
def test_postgres_tombstone_is_concurrent_durable_and_integrity_checked(
    postgres_dsn, runtime_table_prefix
):
    prefix = runtime_table_prefix
    values = stores(postgres_dsn, prefix)
    try:
        with ThreadPoolExecutor(max_workers=6) as executor:
            requested = list(
                executor.map(
                    lambda _: values["tombstones"].record_requested(
                        deployment_id="single-tenant-local",
                        principal_id="local-owner",
                    ),
                    range(12),
                )
            )
        assert len({item.tombstone_ref for item in requested}) == 1
        completed = values["tombstones"].mark(
            requested[0], status="completed"
        )
        restarted = PostgresPrincipalMemoryDeletionTombstoneStore(
            dsn=postgres_dsn,
            table_prefix=prefix,
            schema_mode="validate",
            clock=lambda: NOW + timedelta(seconds=1),
        )
        assert restarted.get(
            deployment_id="single-tenant-local", principal_id="local-owner"
        ) == completed
        tampered = completed.model_copy(update={"principal_id": "other-owner"})
        with pytest.raises(ValueError, match="integrity mismatch"):
            restarted.validate(tampered)
    finally:
        cleanup(postgres_dsn, values)


@pytest.mark.pg_runtime
def test_postgres_safe_ref_survives_restart_and_rejects_changed_fact(
    postgres_dsn, runtime_table_prefix
):
    prefix = runtime_table_prefix
    values = stores(postgres_dsn, prefix)
    fact = make_active_language("en", "e")
    try:
        stored = values["facts"].declare_active(
            fact, exclusive_key="interview_language", now=NOW
        )
        safe_ref = values["refs"].issue(stored)
        restarted = PostgresPrincipalMemorySafeRefStore(
            dsn=postgres_dsn,
            table_prefix=prefix,
            schema_mode="validate",
            clock=lambda: NOW,
        )
        assert restarted.resolve(
            safe_ref,
            deployment_id=stored.deployment_id,
            principal_id=stored.principal_id,
            fact_store=values["facts"],
        ) == stored
        values["facts"].transition(
            deployment_id=stored.deployment_id,
            principal_id=stored.principal_id,
            fact_id=stored.fact_id,
            expected_version=stored.version,
            target_status="revoked",
            now=NOW,
        )
        with pytest.raises(ValueError, match="stale"):
            restarted.resolve(
                safe_ref,
                deployment_id=stored.deployment_id,
                principal_id=stored.principal_id,
                fact_store=values["facts"],
            )
    finally:
        cleanup(postgres_dsn, values)


@pytest.mark.pg_runtime
def test_postgres_full_delete_and_restore_replay_reach_zero_residue(
    postgres_dsn, runtime_table_prefix
):
    prefix = runtime_table_prefix
    values = stores(postgres_dsn, prefix)
    resolver = ExplicitPrincipalIdentityResolver(
        deployment_id="single-tenant-local",
        principal_id="local-owner",
        assurance="trusted_local",
        clock=lambda: NOW,
    )
    control_service = PrincipalMemoryControlService(
        identity_resolver=resolver,
        store=values["controls"],
        clock=lambda: NOW,
    )
    export_service = PrincipalMemoryExportService(
        identity_resolver=resolver,
        lifecycle_service=SafeLifecycle(),
        consent_store=values["consents"],
        control_service=control_service,
        export_store=values["exports"],
        clock=lambda: NOW,
        ref_factory=lambda: "pm-export-" + "c" * 32,
    )
    deletion = PrincipalMemoryDeletionService(
        identity_resolver=resolver,
        consent_store=values["consents"],
        fact_store=values["facts"],
        control_store=values["controls"],
        export_store=values["exports"],
        tombstone_store=values["tombstones"],
        cache_purge=values["refs"].purge,
    )

    def restore_rows():
        fact = make_active_language("mixed", "f")
        stored = values["facts"].declare_active(
            fact, exclusive_key="interview_language", now=NOW
        )
        values["consents"].grant(
            PrincipalMemoryConsent(
                deployment_id="single-tenant-local",
                principal_id="local-owner",
                policy_version="principal-memory-consent-v1",
                allowed_purposes=["fact_storage", "local_consume"],
                granted_at=NOW,
            )
        )
        control_service.set_global_enabled(False)
        export_service.create()
        values["refs"].issue(stored)

    try:
        restore_rows()
        assert deletion.purge_current_principal()["status"] == "completed"
        tombstone = values["tombstones"].get(
            deployment_id="single-tenant-local", principal_id="local-owner"
        )
        assert values["facts"].count_by_principal(
            deployment_id="single-tenant-local", principal_id="local-owner"
        ) == 0
        assert values["exports"].count(
            deployment_id="single-tenant-local", principal_id="local-owner"
        ) == 0

        restore_rows()
        assert deletion.replay(tombstone)["status"] == "replayed"
        assert values["facts"].count_by_principal(
            deployment_id="single-tenant-local", principal_id="local-owner"
        ) == 0
        assert values["consents"].get_current(
            deployment_id="single-tenant-local", principal_id="local-owner"
        ) is None
        assert values["controls"].count(
            deployment_id="single-tenant-local", principal_id="local-owner"
        ) == 0
        assert values["exports"].count(
            deployment_id="single-tenant-local", principal_id="local-owner"
        ) == 0
    finally:
        cleanup(postgres_dsn, values)


@pytest.mark.pg_runtime
def test_postgres_rights_validation_rejects_dirty_existing_schema(
    postgres_dsn, runtime_table_prefix
):
    import psycopg2
    from psycopg2 import sql

    prefix = runtime_table_prefix
    table = f"{prefix}_principal_memory_exports"
    try:
        with psycopg2.connect(postgres_dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL("CREATE TABLE {table} (export_ref TEXT PRIMARY KEY)").format(
                        table=sql.Identifier(table)
                    )
                )
        with pytest.raises(PostgresSchemaNotReady, match="incompatible"):
            PostgresPrincipalMemoryExportStore(
                dsn=postgres_dsn,
                table_prefix=prefix,
                schema_mode="validate",
            )
    finally:
        with psycopg2.connect(postgres_dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL("DROP TABLE IF EXISTS {table} CASCADE").format(
                        table=sql.Identifier(table)
                    )
                )
