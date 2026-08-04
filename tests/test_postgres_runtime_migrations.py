from contextlib import contextmanager
from types import SimpleNamespace

import pytest

import app.services.postgres_runtime_migrations as migrations
from app.services.postgres_connections import PostgresSchemaNotReady
from app.services.postgres_runtime_migrations import (
    BorrowedMigrationConnectionProvider,
    PostgresMigrationConflict,
    migrate_postgres_runtime,
)
from app.services.postgres_schema import validate_relations
from app.services.postgres_schema_contract import LATEST_RUNTIME_MIGRATION
from app.services.postgres_schema_contract import RUNTIME_MIGRATIONS
from app.services.embedding_providers import DisabledEmbeddingProvider
from tests.postgres_support import make_runtime_table_prefix
from scripts.postgres_runtime_migrate import main


class FakeCursor:
    def __init__(self, connection):
        self.connection = connection
        self.row = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, statement, params=None):
        self.connection.calls.append((statement, params))
        if "current_schema" in str(statement):
            self.row = (self.connection.current_schema,)
        elif params == (migrations.RUNTIME_MIGRATION_ID,):
            self.row = self.connection.applied_row
        elif params and len(params) == 1 and isinstance(params[0], int):
            self.connection.lock_calls += 1
            self.row = (True,) if self.connection.lock_calls > 1 else None
        else:
            self.row = None

    def fetchone(self):
        return self.row

    def fetchall(self):
        return self.connection.rows


class FakeConnection:
    def __init__(self, applied_row=None, current_schema="public"):
        self.applied_row = applied_row
        self.current_schema = current_schema
        self.calls = []
        self.rows = []
        self.lock_calls = 0
        self.autocommit = True
        self.closed = False
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


def _patch_schema_owners(monkeypatch, seen):
    def owner(**kwargs):
        provider = kwargs["connection_provider"]
        assert isinstance(provider, BorrowedMigrationConnectionProvider)
        seen.append((kwargs["schema_mode"], provider.connection_object))
        return SimpleNamespace()

    for name in (
        "PostgresInterviewSessionStore",
        "PostgresInterviewGenerationStore",
        "PostgresInterviewWorkflowStore",
        "PostgresReportJobStore",
        "PostgresReviewWorkflowStore",
        "PostgresRuntimeSignalStore",
        "PostgresMemoryMetricStore",
        "PostgresPrincipalMemoryConsentStore",
        "PostgresPrincipalMemoryFactStore",
        "PostgresPrincipalMemoryControlStore",
        "PostgresPrincipalMemoryExportStore",
        "PostgresPrincipalMemoryDeletionTombstoneStore",
        "PostgresPrincipalMemorySafeRefStore",
    ):
        monkeypatch.setattr(migrations, name, owner)

    class Vector:
        def __init__(self, **kwargs):
            owner(**kwargs)

        def ensure_schema(self):
            seen.append(("vector_ensure", None))

    monkeypatch.setattr(migrations, "PgVectorKnowledgeStore", Vector)


def test_migration_uses_one_borrowed_transaction_connection(monkeypatch):
    connection = FakeConnection()
    seen = []
    _patch_schema_owners(monkeypatch, seen)
    setup = []
    monkeypatch.setattr(migrations, "_setup_langgraph_checkpointer", setup.append)

    result = migrate_postgres_runtime(
        dsn="private-dsn",
        table_prefix="test_runtime",
        pgvector_table="knowledge_chunks",
        embedding_provider=object(),
        connect=lambda dsn: connection,
    )

    assert result.applied is True
    assert len([item for item in seen if item[0] == "migrate"]) == 14
    assert all(item[1] is connection for item in seen if item[0] == "migrate")
    assert setup == ["private-dsn"]
    assert connection.commits >= 2
    assert connection.closed is True


def test_migration_checksum_divergence_fails_closed(monkeypatch):
    connection = FakeConnection(applied_row=("different",))
    monkeypatch.setattr(
        migrations,
        "_setup_langgraph_checkpointer",
        lambda dsn: pytest.fail("setup must not run"),
    )

    with pytest.raises(PostgresMigrationConflict):
        migrate_postgres_runtime(
            dsn="private-dsn",
            table_prefix="test_runtime",
            pgvector_table="knowledge_chunks",
            embedding_provider=object(),
            connect=lambda dsn: connection,
        )

    assert connection.rollbacks == 1
    assert connection.closed is True


def test_migration_registry_rejects_diverged_earlier_contract(monkeypatch):
    connection = FakeConnection()
    first = migrations.RUNTIME_MIGRATIONS[0]
    connection.rows = [
        (first.migration_id, "different", first.transaction_mode)
    ]
    monkeypatch.setattr(
        migrations,
        "_setup_langgraph_checkpointer",
        lambda dsn: pytest.fail("setup must not run"),
    )

    with pytest.raises(PostgresMigrationConflict):
        migrate_postgres_runtime(
            dsn="private-dsn",
            table_prefix="test_runtime",
            pgvector_table="knowledge_chunks",
            embedding_provider=object(),
            connect=lambda dsn: connection,
        )


def test_migration_rejects_non_public_search_path(monkeypatch):
    connection = FakeConnection(current_schema="tenant_schema")
    monkeypatch.setattr(
        migrations,
        "_setup_langgraph_checkpointer",
        lambda dsn: pytest.fail("setup must not run"),
    )

    with pytest.raises(PostgresMigrationConflict, match="public schema"):
        migrate_postgres_runtime(
            dsn="private-dsn",
            table_prefix="test_runtime",
            pgvector_table="knowledge_chunks",
            embedding_provider=object(),
            connect=lambda dsn: connection,
        )


def test_migration_cli_defaults_to_safe_dry_run(monkeypatch, capsys):
    monkeypatch.setenv("INTERVIEW_RUNTIME_TABLE_PREFIX", "test_cli")
    monkeypatch.setenv("POSTGRES_DSN", "must-not-be-printed")
    monkeypatch.setattr(
        "scripts.postgres_runtime_migrate.migrate_postgres_runtime",
        lambda **kwargs: pytest.fail("dry-run must not connect"),
    )

    assert main([]) == 0
    output = capsys.readouterr().out
    assert "mode=DRY_RUN" in output
    assert "must-not-be-printed" not in output


class RelationProvider:
    def __init__(self, rows):
        self.connection_object = FakeConnection()
        self.connection_object.rows = rows

    @contextmanager
    def connection(self):
        yield self.connection_object


def test_read_only_schema_validation_rejects_missing_relation():
    provider = RelationProvider([("one", "one"), ("two", None)])

    with pytest.raises(PostgresSchemaNotReady):
        validate_relations(provider, ("one", "two"))


class ContractCursor:
    def __init__(self, *, columns, migration=None):
        self.columns = columns
        self.migration = migration
        self.result = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, statement, params=None):
        text = str(statement)
        if "to_regclass" in text:
            self.result = [(name, name) for name in params[0]]
        elif "information_schema.columns" in text:
            self.result = list(self.columns)
        elif "WHERE migration_id" in text:
            self.result = [self.migration] if self.migration is not None else []

    def fetchall(self):
        return self.result

    def fetchone(self):
        return self.result[0] if self.result else None


class ContractProvider:
    def __init__(self, cursor):
        self.cursor_object = cursor

    @contextmanager
    def connection(self):
        yield SimpleNamespace(cursor=lambda: self.cursor_object)


def test_schema_validation_rejects_existing_table_with_missing_fencing_column():
    table = "test_generation_attempts"
    columns = [
        (table, name)
        for name in (
            "generation_id",
            "attempt_number",
            "status",
            "lease_token",
            "lease_expires_at",
        )
    ]

    with pytest.raises(PostgresSchemaNotReady, match="incompatible"):
        validate_relations(
            ContractProvider(ContractCursor(columns=columns)),
            (table,),
        )


def test_report_job_contract_requires_independent_heartbeat_column():
    from app.services.postgres_schema_contract import required_columns_for_relation

    required = required_columns_for_relation("interview_report_jobs")

    assert "heartbeat_at" in required
    assert "lease_expires_at" in required
    assert "updated_at" not in required or "heartbeat_at" != "updated_at"


def test_local_principal_rights_schema_contract_is_complete():
    from app.services.postgres_schema_contract import required_columns_for_relation

    expected = {
        "interview_principal_memory_controls": {"session_key", "enabled", "version"},
        "interview_principal_memory_exports": {"export_ref", "payload", "expires_at"},
        "interview_principal_memory_tombs": {
            "tombstone_ref", "integrity_sha256", "status"
        },
        "interview_principal_memory_refs": {
            "safe_ref", "fact_id", "fact_version", "expires_at"
        },
    }
    for relation, columns in expected.items():
        assert columns.issubset(required_columns_for_relation(relation))


def test_schema_validation_rejects_missing_latest_migration_row():
    table = "test_schema_migrations"
    columns = [
        (table, name)
        for name in ("migration_id", "checksum", "transaction_mode", "applied_at")
    ]

    with pytest.raises(PostgresSchemaNotReady, match="migration"):
        validate_relations(
            ContractProvider(ContractCursor(columns=columns)),
            (table,),
        )


def test_schema_validation_accepts_latest_migration_contract():
    table = "test_schema_migrations"
    columns = [
        (table, name)
        for name in ("migration_id", "checksum", "transaction_mode", "applied_at")
    ]
    migration = (
        LATEST_RUNTIME_MIGRATION.checksum,
        LATEST_RUNTIME_MIGRATION.transaction_mode,
    )

    validate_relations(
        ContractProvider(ContractCursor(columns=columns, migration=migration)),
        (table,),
    )


@pytest.mark.pg_runtime
def test_actual_migration_installs_heartbeat_and_is_idempotent(postgres_dsn):
    import psycopg2
    from psycopg2 import sql

    prefix = make_runtime_table_prefix("report_heartbeat")
    vector = make_runtime_table_prefix("report_vector")
    try:
        first = migrate_postgres_runtime(
            dsn=postgres_dsn,
            table_prefix=prefix,
            pgvector_table=vector,
            embedding_provider=DisabledEmbeddingProvider(
                model_name="disabled",
                dimension=3,
            ),
            run_checkpointer_setup=False,
        )
        second = migrate_postgres_runtime(
            dsn=postgres_dsn,
            table_prefix=prefix,
            pgvector_table=vector,
            embedding_provider=DisabledEmbeddingProvider(
                model_name="disabled",
                dimension=3,
            ),
            run_checkpointer_setup=False,
        )
        with psycopg2.connect(postgres_dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema = current_schema()
                      AND table_name = %s
                    """,
                    (f"{prefix}_report_jobs",),
                )
                columns = {row[0] for row in cursor.fetchall()}
                cursor.execute(
                    """
                    SELECT table_name
                    FROM information_schema.tables
                    WHERE table_schema = current_schema()
                      AND table_name = ANY(%s::text[])
                    """,
                    ([
                        f"{prefix}_principal_memory_controls",
                        f"{prefix}_principal_memory_exports",
                        f"{prefix}_principal_memory_tombs",
                        f"{prefix}_principal_memory_refs",
                    ],),
                )
                local_rights_tables = {row[0] for row in cursor.fetchall()}

        assert first.applied is True
        assert second.applied is False
        assert first.migration_id == "principal_memory_integrity_v2"
        assert "heartbeat_at" in columns
        assert "lease_expires_at" in columns
        assert local_rights_tables == {
            f"{prefix}_principal_memory_controls",
            f"{prefix}_principal_memory_exports",
            f"{prefix}_principal_memory_tombs",
            f"{prefix}_principal_memory_refs",
        }
    finally:
        with psycopg2.connect(postgres_dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT table_name
                    FROM information_schema.tables
                    WHERE table_schema = current_schema()
                      AND (table_name LIKE %s OR table_name LIKE %s)
                    """,
                    (prefix + "_%", vector + "_%"),
                )
                names = [row[0] for row in cursor.fetchall()]
                for name in names:
                    cursor.execute(
                        sql.SQL("DROP TABLE IF EXISTS {table} CASCADE").format(
                            table=sql.Identifier(name)
                        )
                    )


@pytest.mark.pg_runtime
def test_actual_migration_upgrades_v10_and_runtime_factories_are_durable(
    postgres_dsn, monkeypatch
):
    import psycopg2
    from psycopg2 import sql
    from app.services import runtime

    prefix = make_runtime_table_prefix("principal_rights_upgrade")
    vector = make_runtime_table_prefix("principal_rights_vector")
    migrations_table = f"{prefix}_schema_migrations"
    v10 = RUNTIME_MIGRATIONS[-2]
    try:
        with psycopg2.connect(postgres_dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        "CREATE TABLE {table} (migration_id TEXT PRIMARY KEY,"
                        "checksum TEXT NOT NULL,transaction_mode TEXT NOT NULL,"
                        "applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW())"
                    ).format(table=sql.Identifier(migrations_table))
                )
                cursor.execute(
                    sql.SQL(
                        "INSERT INTO {table} (migration_id,checksum,transaction_mode) "
                        "VALUES (%s,%s,%s)"
                    ).format(table=sql.Identifier(migrations_table)),
                    (v10.migration_id, v10.checksum, v10.transaction_mode),
                )

        result = migrate_postgres_runtime(
            dsn=postgres_dsn,
            table_prefix=prefix,
            pgvector_table=vector,
            embedding_provider=DisabledEmbeddingProvider(
                model_name="disabled", dimension=3
            ),
            run_checkpointer_setup=False,
        )
        assert result.applied is True
        assert result.migration_id == "principal_memory_integrity_v2"

        runtime.reset_runtime_for_tests()
        monkeypatch.setenv("POSTGRES_DSN", postgres_dsn)
        monkeypatch.setenv("INTERVIEW_RUNTIME_STORE", "postgres")
        monkeypatch.setenv("INTERVIEW_RUNTIME_TABLE_PREFIX", prefix)
        monkeypatch.setenv("MEMORY_LOCAL_PRINCIPAL_ENABLED", "true")
        monkeypatch.setenv(
            "MEMORY_TRUSTED_LOCAL_PRINCIPAL_MEMORY_API_ENABLED", "true"
        )
        assert runtime.get_principal_memory_export_store().__class__.__name__ == (
            "PostgresPrincipalMemoryExportStore"
        )
        assert (
            runtime.get_principal_memory_deletion_tombstone_store().__class__.__name__
            == "PostgresPrincipalMemoryDeletionTombstoneStore"
        )
        assert runtime.get_principal_memory_safe_ref_store().__class__.__name__ == (
            "PostgresPrincipalMemorySafeRefStore"
        )
        from fastapi.testclient import TestClient
        from app.main import app

        client = TestClient(app, client=("127.0.0.1", 50000))
        exported = client.post(
            "/api/runtime/principal-memory/export",
            headers={"x-local-memory-action": "1"},
        )
        assert exported.status_code == 200
        assert exported.json()["payload"]["facts"] == []
        deleted = client.delete(
            "/api/runtime/principal-memory",
            headers={"x-local-memory-action": "1"},
        )
        assert deleted.status_code == 200
        assert deleted.json()["status"] == "completed"
        runtime.reset_runtime_for_tests()
    finally:
        runtime.reset_runtime_for_tests()
        with psycopg2.connect(postgres_dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT table_name
                    FROM information_schema.tables
                    WHERE table_schema = current_schema()
                      AND (table_name LIKE %s OR table_name LIKE %s)
                    """,
                    (prefix + "_%", vector + "_%"),
                )
                for (name,) in cursor.fetchall():
                    cursor.execute(
                        sql.SQL("DROP TABLE IF EXISTS {table} CASCADE").format(
                            table=sql.Identifier(name)
                        )
                    )
