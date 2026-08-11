"""PostgreSQL integration coverage."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Event
from datetime import datetime, timedelta, timezone

import pytest

from app.adapters.postgres.principal_memory import PostgresPrincipalMemoryFactStore
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
    InMemoryPrincipalMemoryDeletionTombstoneStore,
    PrincipalMemoryExportRecord,
    PrincipalMemoryExportService,
)
from app.services.principal_memory_ledger import ProtectedPrincipalMemoryLedger
from app.services.principal_memory_ledger_replay import (
    PostgresPrincipalMemoryScopeInventory,
    PrincipalMemoryOpaqueLedgerReplay,
)
from app.services.postgres_principal_memory_ledger import (
    PostgresPrincipalMemoryLedgerWatermarkStore,
)
from app.services.principal_memory_operations import (
    PostgresPrincipalMemoryMigrationProbe,
)
from app.services.postgres_connections import DirectPsycopg2ConnectionProvider
from tests.postgres_support import assert_safe_test_prefix
from tests.integration.postgres.test_postgres_principal_memory import (
    make_active_language,
)


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
    if "watermark" in values:
        tables.add(values["watermark"].table)
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

        import psycopg2
        from psycopg2 import sql

        with psycopg2.connect(postgres_dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL("DELETE FROM {table}").format(
                        table=sql.Identifier(values["tombstones"].table)
                    )
                )
        assert restarted.get(
            deployment_id="single-tenant-local", principal_id="local-owner"
        ) is None
        assert restarted.import_tombstone(completed) == completed
        assert restarted.import_tombstone(completed) == completed
        second = restarted.record_requested(
            deployment_id="single-tenant-local", principal_id="local-owner"
        )
        second = restarted.mark(second, status="completed")
        assert second.tombstone_ref != completed.tombstone_ref
        assert restarted.import_tombstone(completed) == completed
        with psycopg2.connect(postgres_dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL("SELECT COUNT(*) FROM {table}").format(
                        table=sql.Identifier(values["tombstones"].table)
                    )
                )
                assert cursor.fetchone()[0] == 2
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
def test_old_backup_opaque_ledger_replay_advances_watermark_and_prevents_revive(
    postgres_dsn, runtime_table_prefix, tmp_path
):
    prefix = runtime_table_prefix
    values = stores(postgres_dsn, prefix)
    values["watermark"] = PostgresPrincipalMemoryLedgerWatermarkStore(
        dsn=postgres_dsn, table_prefix=prefix, schema_mode="migrate"
    )
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
    deletion = PrincipalMemoryDeletionService(
        identity_resolver=resolver,
        consent_store=values["consents"],
        fact_store=values["facts"],
        control_store=values["controls"],
        export_store=values["exports"],
        tombstone_store=values["tombstones"],
        cache_purge=values["refs"].purge,
        cache_count=values["refs"].count,
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    protected = tmp_path / "protected"
    protected.mkdir()
    ledger = ProtectedPrincipalMemoryLedger(
        protected / "operator.jsonl", workspace=workspace
    )

    # The external event represents deletion truth newer than the restored DB.
    event_store = InMemoryPrincipalMemoryDeletionTombstoneStore(clock=lambda: NOW)
    requested = event_store.record_requested(
        deployment_id="single-tenant-local", principal_id="local-owner"
    )
    ledger.append_tombstone(event_store.completion_candidate(requested))

    try:
        fact = make_active_language("mixed", "e")
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
        values["refs"].issue(stored)

        result = PrincipalMemoryOpaqueLedgerReplay(
            ledger=ledger,
            watermark_store=values["watermark"],
            scope_inventory=PostgresPrincipalMemoryScopeInventory(
                connection_provider=DirectPsycopg2ConnectionProvider(postgres_dsn),
                table_prefix=prefix,
            ),
            deletion_service=deletion,
        ).replay_missing()

        assert result["events_replayed"] == 1
        assert values["facts"].count_by_principal(
            deployment_id="single-tenant-local", principal_id="local-owner"
        ) == 0
        assert values["consents"].get_current(
            deployment_id="single-tenant-local", principal_id="local-owner"
        ) is None
        assert values["controls"].count(
            deployment_id="single-tenant-local", principal_id="local-owner"
        ) == 0
        assert values["refs"].count(
            deployment_id="single-tenant-local", principal_id="local-owner"
        ) == 0
        assert values["watermark"].get().last_applied_ledger_event_count == 1
        rendered = repr(result)
        assert "local-owner" not in rendered
        assert str(ledger.resolved_path) not in rendered
    finally:
        cleanup(postgres_dsn, values)


@pytest.mark.pg_runtime
def test_postgres_deletion_fence_rejects_concurrent_consent_writer(
    postgres_dsn, runtime_table_prefix
):
    values = stores(postgres_dsn, runtime_table_prefix)
    resolver = ExplicitPrincipalIdentityResolver(
        deployment_id="single-tenant-local",
        principal_id="local-owner",
        assurance="trusted_local",
        clock=lambda: NOW,
    )
    values["consents"].grant(
        PrincipalMemoryConsent(
            deployment_id="single-tenant-local",
            principal_id="local-owner",
            policy_version="principal-memory-consent-v1",
            allowed_purposes=["fact_storage"],
            granted_at=NOW,
        )
    )
    entered = Event()
    release = Event()

    def inject(stage):
        if stage == "consent":
            entered.set()
            assert release.wait(timeout=10)

    deletion = PrincipalMemoryDeletionService(
        identity_resolver=resolver,
        consent_store=values["consents"],
        fact_store=values["facts"],
        control_store=values["controls"],
        export_store=values["exports"],
        tombstone_store=values["tombstones"],
        cache_purge=values["refs"].purge,
        failure_injector=inject,
    )

    def write_consent():
        assert entered.wait(timeout=10)
        with values["tombstones"].writer_guard(
            deployment_id="single-tenant-local",
            principal_id="local-owner",
        ):
            return values["consents"].grant(
                PrincipalMemoryConsent(
                    deployment_id="single-tenant-local",
                    principal_id="local-owner",
                    policy_version="principal-memory-consent-v1",
                    allowed_purposes=["fact_storage", "local_consume"],
                    granted_at=NOW,
                )
            )

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            deleting = executor.submit(deletion.purge_current_principal)
            writing = executor.submit(write_consent)
            assert entered.wait(timeout=10)
            release.set()
            assert deleting.result()["status"] == "completed"
            with pytest.raises(PermissionError, match="deletion fence"):
                writing.result()
        assert values["consents"].get_current(
            deployment_id="single-tenant-local", principal_id="local-owner"
        ) is None
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


@pytest.mark.pg_runtime
def test_postgres_expiry_cleanup_is_bounded_and_preserves_live_records(
    postgres_dsn, runtime_table_prefix
):
    prefix = runtime_table_prefix
    values = stores(postgres_dsn, prefix)
    expired = PrincipalMemoryExportRecord(
        export_ref="pm-export-" + "d" * 32,
        deployment_id="single-tenant-local",
        principal_id="local-owner",
        payload={},
        created_at=NOW - timedelta(hours=48),
        expires_at=NOW - timedelta(hours=24),
    )
    live = expired.model_copy(
        update={
            "export_ref": "pm-export-" + "e" * 32,
            "created_at": NOW,
            "expires_at": NOW + timedelta(hours=24),
        }
    )
    try:
        values["exports"].put(expired)
        values["exports"].put(live)
        fact = values["facts"].declare_active(
            make_active_language("en", "9"),
            exclusive_key="interview_language",
            now=NOW,
        )
        values["refs"].issue(fact)

        assert values["exports"].cleanup_expired(now=NOW, batch_size=1) == 1
        assert values["exports"].cleanup_expired(now=NOW, batch_size=1) == 0
        assert values["exports"].get(live.export_ref, now=NOW) == live
        assert values["refs"].cleanup_expired(
            now=NOW + timedelta(minutes=16), batch_size=1
        ) == 1
        assert values["refs"].cleanup_expired(
            now=NOW + timedelta(minutes=16), batch_size=1
        ) == 0
    finally:
        cleanup(postgres_dsn, values)


@pytest.mark.pg_runtime
def test_postgres_local_memory_migration_probe_checks_id_and_checksum(
    postgres_dsn, runtime_table_prefix
):
    import psycopg2
    from psycopg2 import sql

    prefix = runtime_table_prefix
    table = f"{prefix}_schema_migrations"
    provider = DirectPsycopg2ConnectionProvider(postgres_dsn)
    probe = PostgresPrincipalMemoryMigrationProbe(
        connection_provider=provider,
        table_prefix=prefix,
        migration_id="principal_memory_local_rights_v1",
        checksum="a" * 64,
    )
    try:
        assert probe.is_current() is False
        with psycopg2.connect(postgres_dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        "CREATE TABLE {table} (migration_id TEXT PRIMARY KEY, "
                        "checksum TEXT NOT NULL)"
                    ).format(table=sql.Identifier(table))
                )
                cursor.execute(
                    sql.SQL(
                        "INSERT INTO {table} (migration_id,checksum) VALUES (%s,%s)"
                    ).format(table=sql.Identifier(table)),
                    ("principal_memory_local_rights_v1", "a" * 64),
                )
        assert probe.is_current() is True
        with psycopg2.connect(postgres_dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL("UPDATE {table} SET checksum=%s").format(
                        table=sql.Identifier(table)
                    ),
                    ("b" * 64,),
                )
        assert probe.is_current() is False
    finally:
        with psycopg2.connect(postgres_dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL("DROP TABLE IF EXISTS {table} CASCADE").format(
                        table=sql.Identifier(table)
                    )
                )


@pytest.mark.pg_runtime
def test_concurrent_postgres_export_cleanup_deletes_each_expired_row_once(
    postgres_dsn, runtime_table_prefix
):
    prefix = runtime_table_prefix
    values = stores(postgres_dsn, prefix)
    try:
        for index in range(20):
            values["exports"].put(
                PrincipalMemoryExportRecord(
                    export_ref=f"pm-export-{index:032x}",
                    deployment_id="single-tenant-local",
                    principal_id="local-owner",
                    payload={},
                    created_at=NOW - timedelta(hours=48),
                    expires_at=NOW - timedelta(hours=24),
                )
            )
        with ThreadPoolExecutor(max_workers=4) as executor:
            counts = list(
                executor.map(
                    lambda _: values["exports"].cleanup_expired(
                        now=NOW, batch_size=3
                    ),
                    range(12),
                )
            )
        assert sum(counts) == 20
        assert values["exports"].count(
            deployment_id="single-tenant-local",
            principal_id="local-owner",
        ) == 0
    finally:
        cleanup(postgres_dsn, values)
