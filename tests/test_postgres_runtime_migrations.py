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
from app.services.postgres_schema_contract import required_columns_for_relation
from app.services.embedding_providers import DisabledEmbeddingProvider
from app.services.postgres_identifiers import runtime_schema_identifier
from app.services.postgres_principal_memory import PostgresPrincipalMemoryFactStore
from app.services.postgres_session import PostgresInterviewSessionStore
from app.services.prep import InterviewPlan, InterviewQuestion
from tests.postgres_support import make_runtime_table_prefix
from tests.test_postgres_principal_memory import NOW, make_active_language
from scripts.postgres_runtime_migrate import main


@pytest.mark.pg_runtime
def test_session_plan_binding_backfill_marks_legacy_snapshot(postgres_dsn):
    import psycopg2
    from psycopg2 import sql

    prefix = make_runtime_table_prefix("plan_binding")
    store = PostgresInterviewSessionStore(
        dsn=postgres_dsn,
        table_prefix=prefix,
        schema_mode="migrate",
    )
    turn = store.start(
        InterviewPlan(
            title="Legacy plan",
            questions=[
                InterviewQuestion(
                    id="q1",
                    kind="technical",
                    prompt="Explain caching.",
                    focus="cache",
                )
            ],
        ),
        job_description="Backend role",
        resume_text="Built APIs",
        job_tags=["backend"],
    )
    try:
        with psycopg2.connect(postgres_dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        "UPDATE {sessions} SET plan_binding_json=NULL WHERE session_id=%s"
                    ).format(sessions=sql.Identifier(store.sessions_table)),
                    (turn.session_id,),
                )
            migrations._upgrade_session_plan_bindings(
                connection,
                table_prefix=prefix,
            )
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        "SELECT plan_binding_json FROM {sessions} WHERE session_id=%s"
                    ).format(sessions=sql.Identifier(store.sessions_table)),
                    (turn.session_id,),
                )
                binding = cursor.fetchone()[0]

        assert binding["plan_origin"] == "legacy_session_snapshot"
        assert binding["plan_revision_id"] is None
        assert binding["plan_snapshot"]["title"] == "Legacy plan"
        assert len(binding["plan_sha256"]) == 64
    finally:
        with psycopg2.connect(postgres_dsn) as connection:
            with connection.cursor() as cursor:
                for table in (
                    store.question_evaluations_table,
                    store.reports_table,
                    store.messages_table,
                    store.sessions_table,
                    f"{prefix}_runtime_outbox",
                    f"{prefix}_runtime_event_receipts",
                    f"{prefix}_agent_runs",
                ):
                    cursor.execute(
                        sql.SQL("DROP TABLE IF EXISTS {table} CASCADE").format(
                            table=sql.Identifier(table)
                        )
                    )


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


def test_v15_prep_plan_versions_uses_specific_contract_not_pgvector_suffix():
    columns = required_columns_for_relation("interview_prep_plan_versions")
    assert "public_snapshot_json" in columns
    assert "embedding" not in columns


def test_v16_context_artifact_identity_contract_requires_versioned_columns():
    from app.services.postgres_schema_contract import (
        RUNTIME_SCHEMA_V15_CHECKSUM,
        RUNTIME_SCHEMA_V16_CHECKSUM,
        RUNTIME_SCHEMA_V16_MANIFEST,
        required_check_tokens_for_relation,
    )

    columns = required_columns_for_relation("interview_context_artifacts")

    assert {
        "identity_schema_version",
        "compression_intent_sha256",
    }.issubset(columns)
    assert RUNTIME_SCHEMA_V15_CHECKSUM == (
        "e611aad12ce1929d323249c5adb2c90b33a057bc313fd834d7fbf3fcf95cc52e"
    )
    assert RUNTIME_SCHEMA_V16_CHECKSUM == (
        "f0381a784430bca592cc33ecf5d96ad4d989f9ab9ac7c50d14d4693fa2e3c8b6"
    )
    checks = required_check_tokens_for_relation("interview_context_artifacts")
    assert any(
        {
            "identity_schema_version",
            "compression_intent_sha256",
            "identity-v1",
        }.issubset(tokens)
        for tokens in checks
    )
    assert RUNTIME_SCHEMA_V16_CHECKSUM != RUNTIME_SCHEMA_V15_CHECKSUM
    assert (
        f'"base_schema_checksum":"{RUNTIME_SCHEMA_V15_CHECKSUM}"'
        in RUNTIME_SCHEMA_V16_MANIFEST
    )
    assert RUNTIME_MIGRATIONS[15].migration_id == "context_artifact_identity_v1_v16"
    assert RUNTIME_MIGRATIONS[15].checksum == RUNTIME_SCHEMA_V16_CHECKSUM
    assert LATEST_RUNTIME_MIGRATION.migration_id == (
        "question_memory_resolved_target_v1_v27"
    )


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


class PersistentMigrationDatabase:
    def __init__(self, rows):
        self.rows = list(rows)
        self.connections = []

    def connect(self, dsn):
        connection = PersistentMigrationConnection(self)
        self.connections.append(connection)
        return connection


class PersistentMigrationConnection:
    def __init__(self, database):
        self.database = database
        self.calls = []
        self.lock_calls = 0
        self.autocommit = True
        self.closed = False
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return PersistentMigrationCursor(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


class PersistentMigrationCursor:
    def __init__(self, connection):
        self.connection = connection
        self.row = None
        self.result_rows = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, statement, params=None):
        statement_text = str(statement)
        self.connection.calls.append((statement, params))
        self.row = None
        self.result_rows = []
        if "current_schema" in statement_text:
            self.row = ("public",)
        elif (
            "SELECT checksum FROM" in statement_text
            and params == (migrations.RUNTIME_MIGRATION_ID,)
        ):
            matching = next(
                (
                    row
                    for row in self.connection.database.rows
                    if row[0] == migrations.RUNTIME_MIGRATION_ID
                ),
                None,
            )
            self.row = None if matching is None else (matching[1],)
        elif "SELECT migration_id, checksum, transaction_mode" in statement_text:
            requested_ids = set(params[0])
            self.result_rows = [
                row
                for row in self.connection.database.rows
                if row[0] in requested_ids
            ]
        elif (
            "INSERT INTO" in statement_text
            and params is not None
            and len(params) == 3
            and params[0]
            in {spec.migration_id for spec in migrations.RUNTIME_MIGRATIONS}
        ):
            if not any(
                row[0] == params[0] for row in self.connection.database.rows
            ):
                self.connection.database.rows.append(tuple(params))
        elif params and len(params) == 1 and isinstance(params[0], int):
            self.connection.lock_calls += 1
            self.row = (
                (True,) if self.connection.lock_calls > 1 else None
            )

    def fetchone(self):
        return self.row

    def fetchall(self):
        return self.result_rows


class IdentityUpgradeCursor:
    def __init__(self, connection):
        self.connection = connection
        self.row = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, statement, params=None):
        rendered = str(statement)
        self.connection.calls.append((rendered, params))
        if "SELECT 1 FROM pg_constraint" in rendered:
            self.row = (1,) if self.connection.constraint_exists else None
        else:
            self.row = None
        if "ADD CONSTRAINT" in rendered:
            self.connection.constraint_exists = True

    def fetchone(self):
        return self.row

class IdentityUpgradeConnection:
    def __init__(self):
        self.calls = []
        self.constraint_exists = False

    def cursor(self):
        return IdentityUpgradeCursor(self)


def test_context_artifact_identity_upgrade_is_idempotent_and_never_backfills():
    connection = IdentityUpgradeConnection()

    migrations._upgrade_context_artifact_identity_v1(
        connection,
        table_prefix="interview",
    )
    migrations._upgrade_context_artifact_identity_v1(
        connection,
        table_prefix="interview",
    )

    statements = "\n".join(statement for statement, _ in connection.calls)
    assert statements.count("ADD COLUMN IF NOT EXISTS identity_schema_version") == 2
    assert statements.count("ADD CONSTRAINT") == 1
    assert "identity_schema_version IS NOT NULL" in statements
    assert "compression_intent_sha256 IS NOT NULL" in statements
    assert "identity-v1" in statements
    assert "UPDATE" not in statements


def _versioned_artifact_rows():
    from app.services.context_artifacts import (
        ContextArtifactIdentity,
        ContextArtifactIdentityMaterial,
    )

    material = ContextArtifactIdentityMaterial(
        artifact_type="question_conversation",
        privacy_scope_sha256="1" * 64,
        source_sha256="2" * 64,
        source_manifest_sha256=None,
        semantic_focus_sha256="3" * 64,
        compression_policy_version="conversation-v1",
        prompt_contract_version="prompt-v1",
        output_schema_version="question-conversation-v1",
        compressor_provider="openai-compatible",
        compressor_model="gpt-4o",
        compressor_settings_sha256="4" * 64,
        target_output_tokens=256,
        identity_schema_version="identity-v1",
        compression_intent_sha256="6" * 64,
    )
    identity = ContextArtifactIdentity.from_material(material)
    identity_values = [
        material.artifact_type,
        material.privacy_scope_sha256,
        material.source_sha256,
        material.source_manifest_sha256,
        material.semantic_focus_sha256,
        material.compression_policy_version,
        material.prompt_contract_version,
        material.output_schema_version,
        material.compressor_provider,
        material.compressor_model,
        material.compressor_settings_sha256,
        material.target_output_tokens,
    ]
    ordinary = [None] * 26
    ordinary[1] = identity.artifact_key
    ordinary[2:14] = identity_values
    ordinary[24:26] = [
        material.identity_schema_version,
        material.compression_intent_sha256,
    ]
    joined = [None] * 25
    joined[5] = identity.artifact_key
    joined[6:18] = identity_values
    joined[23:25] = ordinary[24:26]
    return identity, ordinary, joined


def test_postgres_identity_row_layout_reconstructs_full_v1_identity():
    from app.services.context_artifact_store import PostgresContextArtifactStore

    identity, ordinary, joined = _versioned_artifact_rows()

    assert PostgresContextArtifactStore._identity_from_row(ordinary) == identity
    assert (
        PostgresContextArtifactStore._identity_from_joined_ref_row(joined)
        == identity
    )


@pytest.mark.parametrize(
    ("identity_schema_version", "compression_intent_sha256", "tamper_key"),
    (
        ("identity-v1", None, False),
        (None, "6" * 64, False),
        ("identity-v2", "6" * 64, False),
        ("identity-v1", "not-a-digest", False),
        ("identity-v1", "6" * 64, True),
    ),
)
def test_postgres_identity_row_loaders_fail_closed(
    identity_schema_version,
    compression_intent_sha256,
    tamper_key,
):
    from app.services.context_artifact_store import PostgresContextArtifactStore

    _, ordinary, joined = _versioned_artifact_rows()
    ordinary[24:26] = [
        identity_schema_version,
        compression_intent_sha256,
    ]
    joined[23:25] = ordinary[24:26]
    if tamper_key:
        ordinary[1] = "0" * 64
        joined[5] = "0" * 64

    with pytest.raises(ValueError):
        PostgresContextArtifactStore._identity_from_row(ordinary)
    with pytest.raises(ValueError):
        PostgresContextArtifactStore._identity_from_joined_ref_row(joined)


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
        "PostgresPrincipalMemoryLedgerWatermarkStore",
    ):
        monkeypatch.setattr(migrations, name, owner)

    class Vector:
        def __init__(self, **kwargs):
            owner(**kwargs)

        def ensure_schema(self):
            seen.append(("vector_ensure", None))

    monkeypatch.setattr(migrations, "PgVectorKnowledgeStore", Vector)
    monkeypatch.setattr(
        migrations,
        "_upgrade_principal_memory_exclusive_scope",
        lambda connection, *, table_prefix: seen.append(
            ("exclusive_scope_upgrade", connection)
        ),
    )
    monkeypatch.setattr(
        migrations,
        "_upgrade_context_artifact_identity_v1",
        lambda connection, *, table_prefix: seen.append(
            ("context_artifact_identity_v1_upgrade", connection)
        ),
    )
    monkeypatch.setattr(
        migrations,
        "_upgrade_interview_draft_plan_binding",
        lambda connection, *, table_prefix: seen.append(
            ("interview_draft_plan_binding_upgrade", connection)
        ),
    )


def _patch_full_schema_owners(monkeypatch, seen):
    def owner(label):
        def build(**kwargs):
            provider = kwargs["connection_provider"]
            assert isinstance(provider, BorrowedMigrationConnectionProvider)
            seen.append(label)
            return SimpleNamespace()

        return build

    for attribute in (
        "PostgresInterviewSessionStore",
        "PostgresInterviewWorkflowStore",
        "PostgresReportJobStore",
        "PostgresReviewWorkflowStore",
        "PostgresRuntimeSignalStore",
        "PostgresContextArtifactStore",
        "PostgresMemoryMetricStore",
        "PostgresPrincipalMemoryConsentStore",
        "PostgresPrincipalMemoryFactStore",
        "PostgresPrincipalMemoryControlStore",
        "PostgresPrincipalMemoryExportStore",
        "PostgresPrincipalMemoryDeletionTombstoneStore",
        "PostgresPrincipalMemorySafeRefStore",
        "PostgresPrincipalMemoryLedgerWatermarkStore",
    ):
        monkeypatch.setattr(migrations, attribute, owner(attribute))

    monkeypatch.setattr(
        migrations,
        "PostgresInterviewGenerationStore",
        owner("PostgresInterviewGenerationStore"),
    )
    monkeypatch.setattr(
        migrations,
        "PgVectorKnowledgeStore",
        lambda **kwargs: SimpleNamespace(ensure_schema=lambda: seen.append("vector")),
    )
    for attribute in (
        "_upgrade_principal_memory_exclusive_scope",
        "_upgrade_interview_draft_plan_binding",
        "_upgrade_session_plan_bindings",
        "_upgrade_interview_workflow_engine_constraint",
        "_upgrade_interview_memory_policy_constraint",
    ):
        monkeypatch.setattr(
            migrations,
            attribute,
            lambda connection, *, table_prefix, _attribute=attribute: seen.append(
                _attribute
            ),
        )

    local_owners = (
        ("app.services.postgres_question_memory_index", "PostgresQuestionMemoryIndexStore"),
        ("app.services.postgres_session_deletion", "PostgresSessionDeletionJobStore"),
        (
            "app.services.postgres_session_deletion_tombstones",
            "PostgresSessionDeletionTombstoneStore",
        ),
        ("app.services.postgres_draft_store", "PostgresDraftStore"),
        ("app.services.postgres_prep_plan_store", "PostgresPrepPlanStore"),
        (
            "app.services.postgres_interview_launch_repository",
            "PostgresInterviewLaunchRepository",
        ),
        (
            "app.services.postgres_report_artifact_store",
            "PostgresReportArtifactStore",
        ),
        ("app.services.postgres_decision_store", "PostgresDecisionStore"),
        (
            "app.services.postgres_plan_revision_store",
            "PostgresInterviewPlanRevisionStore",
        ),
    )
    for module_name, attribute in local_owners:
        monkeypatch.setattr(f"{module_name}.{attribute}", owner(attribute))


def test_fresh_install_records_full_registry_and_quality_schema(monkeypatch):
    database = PersistentMigrationDatabase([])
    schema_owners = []
    _patch_full_schema_owners(monkeypatch, schema_owners)
    monkeypatch.setattr(migrations, "_setup_langgraph_checkpointer", lambda dsn: None)

    result = migrate_postgres_runtime(
        dsn="private-dsn",
        table_prefix="test_runtime",
        pgvector_table="knowledge_chunks",
        embedding_provider=object(),
        connect=database.connect,
        run_checkpointer_setup=False,
    )

    assert result.applied is True
    assert database.rows == [
        (spec.migration_id, spec.checksum, spec.transaction_mode)
        for spec in RUNTIME_MIGRATIONS
    ]
    assert len(database.rows) == 27
    assert len({row[0] for row in database.rows}) == 27
    assert {
        "PostgresInterviewPlanRevisionStore",
        "_upgrade_interview_draft_plan_binding",
        "PostgresReportArtifactStore",
        "PostgresDecisionStore",
        "PostgresInterviewGenerationStore",
        "_upgrade_session_plan_bindings",
    }.issubset(schema_owners)


def test_v15_upgrade_preserves_history_and_appends_v16_through_v26(monkeypatch):
    expected_context_history = (
        ("stage48_runtime_schema_v1", "84b2fae3965237b69fb98c8f72c97f9e572c8bf09d93321d91b909cd307fd5b1"),
        ("stage48_runtime_schema_v2_contract", "6602195b698364d335b207d783fefd260d2757e9b8ef79ade84d705bd23d9185"),
        ("stage50_context_artifacts_and_interview_v2", "6650d4055da546ed273663fa14e694c182087a3668161055085ae097975dd8b4"),
        ("memory_session_policy_v1", "f0ce85d19bc1ded2c9568af7a83949855de53ea6a68080798033af2357abb92a"),
        ("question_memory_index_v1", "a08664e58a20c94b0fcad29bad8edd662d0eebc645006d58da4838974d58287c"),
        ("session_deletion_v1", "b95dc781234f4e9403d1b296515b9ccecd052773eb334304e403c650a7a76363"),
        ("session_deletion_tombstone_v1", "95854e4b64060dff1df149a14e6bfd976bc3e10f2eb8b739e79c25ed45cd9594"),
        ("memory_metric_bucket_v1", "b28ed7fc4c2c1a13282e72aa8ba84859682b909e0f4a89587d7612c9bf10bd62"),
        ("principal_memory_v1", "0d13632d37bb1b9e7cfa6453ecbef5e6d54fb9bb16c8fdb4d4a49c0fc523e90e"),
        ("report_job_heartbeat_v1", "923d4fd88b9538233d80075bbb1ba9e453893814fe63fd463fa5ed7c6d18e974"),
        ("principal_memory_local_rights_v1", "61c8036ae35e1fbf028096843072a4895cfee6d156794078df3da42626221fad"),
        ("principal_memory_integrity_v2", "57b3795ff43fc771dbd5ec1297ea8e6949ef1c285ab52489d4dfc81def8b1009"),
        ("principal_memory_exclusive_scope_v3", "a15edf0da09848d0732a8cafacb02a63391cc38c4a3abb8b3a540a3c2231fa0c"),
        ("principal_memory_ledger_watermark_v4", "e6f4844bbb88e165fb1b05347c27d1fc47ef0242fefa38560ac69e8994ac5b98"),
        ("frontend_product_experience_v15", "e611aad12ce1929d323249c5adb2c90b33a057bc313fd834d7fbf3fcf95cc52e"),
    )
    initial_rows = [
        (migration_id, checksum, RUNTIME_MIGRATIONS[index].transaction_mode)
        for index, (migration_id, checksum) in enumerate(expected_context_history)
    ]
    assert [
        (spec.migration_id, spec.checksum)
        for spec in RUNTIME_MIGRATIONS[:15]
    ] == list(expected_context_history)

    database = PersistentMigrationDatabase(initial_rows)
    schema_owners = []
    _patch_full_schema_owners(monkeypatch, schema_owners)
    monkeypatch.setattr(migrations, "_setup_langgraph_checkpointer", lambda dsn: None)

    first = migrate_postgres_runtime(
        dsn="private-dsn",
        table_prefix="test_runtime",
        pgvector_table="knowledge_chunks",
        embedding_provider=object(),
        connect=database.connect,
        run_checkpointer_setup=False,
    )

    expected_rows = [
        (spec.migration_id, spec.checksum, spec.transaction_mode)
        for spec in RUNTIME_MIGRATIONS
    ]
    assert first.applied is True
    assert database.rows[:15] == initial_rows
    assert database.rows == expected_rows
    assert [row[0] for row in database.rows[15:]] == [
        spec.migration_id for spec in RUNTIME_MIGRATIONS[15:]
    ]
    assert {
        "PostgresInterviewPlanRevisionStore",
        "_upgrade_interview_draft_plan_binding",
        "PostgresReportArtifactStore",
        "PostgresDecisionStore",
        "PostgresInterviewGenerationStore",
        "_upgrade_session_plan_bindings",
    }.issubset(schema_owners)

    owner_calls_after_first = list(schema_owners)
    second = migrate_postgres_runtime(
        dsn="private-dsn",
        table_prefix="test_runtime",
        pgvector_table="knowledge_chunks",
        embedding_provider=object(),
        connect=database.connect,
        run_checkpointer_setup=False,
    )

    assert second.applied is False
    assert database.rows == expected_rows
    assert schema_owners == owner_calls_after_first
    assert len(database.connections) == 2
    assert all(connection.closed for connection in database.connections)


def test_v16_upgrade_preserves_canonical_history_and_appends_v17_through_v26(
    monkeypatch,
):
    initial_rows = [
        (spec.migration_id, spec.checksum, spec.transaction_mode)
        for spec in RUNTIME_MIGRATIONS[:16]
    ]
    database = PersistentMigrationDatabase(initial_rows)
    schema_owners = []
    _patch_full_schema_owners(monkeypatch, schema_owners)
    monkeypatch.setattr(migrations, "_setup_langgraph_checkpointer", lambda dsn: None)

    result = migrate_postgres_runtime(
        dsn="private-dsn",
        table_prefix="test_runtime",
        pgvector_table="knowledge_chunks",
        embedding_provider=object(),
        connect=database.connect,
        run_checkpointer_setup=False,
    )

    assert result.applied is True
    assert database.rows[:16] == initial_rows
    assert database.rows[16:] == [
        (spec.migration_id, spec.checksum, spec.transaction_mode)
        for spec in RUNTIME_MIGRATIONS[16:]
    ]


def test_migration_uses_one_borrowed_transaction_connection(monkeypatch):
    connection = FakeConnection()
    seen = []
    _patch_schema_owners(monkeypatch, seen)
    resolved_target_upgrades = []
    monkeypatch.setattr(
        migrations,
        "_upgrade_question_memory_resolved_target_v1",
        lambda upgraded_connection, *, table_prefix: (
            resolved_target_upgrades.append(
                (upgraded_connection, table_prefix)
            )
        ),
    )
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
    assert len([item for item in seen if item[0] == "migrate"]) == 15
    assert all(item[1] is connection for item in seen if item[0] == "migrate")
    assert ("context_artifact_identity_v1_upgrade", connection) in seen
    assert resolved_target_upgrades == [(connection, "test_runtime")]
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
    def __init__(
        self,
        *,
        columns,
        migration=None,
        indexes=None,
        checks=None,
        foreign_keys=None,
    ):
        self.columns = columns
        self.migration = migration
        self.indexes = list(indexes or [])
        self.checks = list(checks or [])
        self.foreign_keys = list(foreign_keys or [])
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
        elif "FROM pg_indexes" in text:
            self.result = list(self.indexes)
        elif "rule.contype='c'" in text:
            self.result = list(self.checks)
        elif "rule.contype='f'" in text:
            self.result = list(self.foreign_keys)
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
        "interview_principal_memory_ledger_watermark": {
            "singleton_key",
            "schema_version",
            "last_applied_ledger_event_count",
            "last_applied_ledger_head_sha256",
            "last_applied_at",
        },
    }
    for relation, columns in expected.items():
        assert columns.issubset(required_columns_for_relation(relation))


def test_principal_fact_schema_contract_owns_taxonomy_scope_columns_and_index():
    from app.services.postgres_schema_contract import (
        required_check_tokens_for_relation,
        required_columns_for_relation,
        required_index_tokens_for_relation,
    )

    relation = "interview_principal_memory_facts"
    assert {
        "taxonomy_key",
        "exclusive_scope_key",
    }.issubset(required_columns_for_relation(relation))
    requirements = required_index_tokens_for_relation(relation)
    assert any("exclusive_scope_key" in tokens for tokens in requirements)
    checks = required_check_tokens_for_relation(relation)
    assert any(
        {"taxonomy_key", "exclusive_scope_key"}.issubset(tokens)
        for tokens in checks
    )


def test_ledger_watermark_contract_owns_columns_and_database_checks():
    from app.services.postgres_schema_contract import (
        required_check_tokens_for_relation,
        required_columns_for_relation,
    )

    relation = "interview_principal_memory_ledger_watermark"
    assert {
        "singleton_key",
        "schema_version",
        "last_applied_ledger_event_count",
        "last_applied_ledger_head_sha256",
        "last_applied_at",
    }.issubset(required_columns_for_relation(relation))
    checks = required_check_tokens_for_relation(relation)
    assert any("singleton_key" in tokens for tokens in checks)
    assert any("schema_version" in tokens for tokens in checks)


def test_decision_attempt_contract_requires_usage_and_trace_checks():
    from app.services.postgres_schema_contract import (
        required_check_tokens_for_relation,
        required_columns_for_relation,
    )

    relation = "interview_decision_attempts"
    assert {
        "cached_input_tokens",
        "provider_response_id_sha256",
    }.issubset(required_columns_for_relation(relation))
    checks = required_check_tokens_for_relation(relation)
    assert any(
        {"duration_ms", "input_tokens", "output_tokens", "provider_invocations"}
        .issubset(tokens)
        for tokens in checks
    )
    assert any(
        {
            "cached_input_tokens",
            "input_tokens",
            "cached_input_tokens<=input_tokens",
            "provider_response_id_sha256",
            "provider_response_id_sha256~^[0-9a-f]{64}$",
        }.issubset(tokens)
        for tokens in checks
    )


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


def test_draft_schema_validation_requires_binding_checks_index_and_foreign_key():
    from app.services.postgres_schema_contract import (
        required_columns_for_relation,
        required_foreign_key_tokens_for_relation,
    )

    table = "test_interview_drafts"
    columns = [(table, name) for name in required_columns_for_relation(table)]
    indexes = [
        (table, f"CREATE INDEX ON {table} (expires_at)"),
        (
            table,
            f"CREATE INDEX ON {table} (latest_plan_revision_id) "
            "WHERE deleted_at IS NULL AND latest_plan_revision_id IS NOT NULL",
        ),
    ]
    checks = [
        (
            table,
            "CHECK (((plan_family_id IS NULL AND latest_plan_revision_id IS NULL "
            "AND plan_source_sha256 IS NULL) OR (plan_family_id IS NOT NULL "
            "AND latest_plan_revision_id IS NOT NULL AND plan_source_sha256 IS NOT NULL "
            "AND plan_source_sha256 ~ '^[0-9a-f]{64}$')))",
        ),
        (table, "CHECK (draft_version > 0)"),
    ]
    foreign_keys = [
        (
            table,
            "FOREIGN KEY (latest_plan_revision_id) REFERENCES "
            "test_plan_revisions(plan_revision_id) ON DELETE RESTRICT",
        )
    ]

    assert required_foreign_key_tokens_for_relation(table)
    validate_relations(
        ContractProvider(
            ContractCursor(
                columns=columns,
                indexes=indexes,
                checks=checks,
                foreign_keys=foreign_keys,
            )
        ),
        (table,),
    )
    with pytest.raises(PostgresSchemaNotReady, match="foreign keys"):
        validate_relations(
            ContractProvider(
                ContractCursor(
                    columns=columns,
                    indexes=indexes,
                    checks=checks,
                    foreign_keys=[],
                )
            ),
            (table,),
        )


@pytest.mark.pg_runtime
def test_actual_migration_installs_heartbeat_and_is_idempotent(postgres_dsn):
    import psycopg2
    from psycopg2 import sql
    from app.services.postgres_decision_store import PostgresDecisionStore

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
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema = current_schema()
                      AND table_name = %s
                    """,
                    (f"{prefix}_generations",),
                )
                generation_columns = {row[0] for row in cursor.fetchall()}
                cursor.execute(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema = current_schema()
                      AND table_name = %s
                    """,
                    (f"{prefix}_followup_decisions",),
                )
                decision_columns = {row[0] for row in cursor.fetchall()}
                cursor.execute(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema = current_schema()
                      AND table_name = %s
                    """,
                    (f"{prefix}_decision_attempts",),
                )
                decision_attempt_columns = {row[0] for row in cursor.fetchall()}
                cursor.execute(
                    """
                    SELECT conname, pg_get_constraintdef(oid)
                    FROM pg_constraint
                    WHERE conrelid = to_regclass(%s)
                      AND contype = 'c'
                    """,
                    (f"public.{prefix}_decision_attempts",),
                )
                decision_attempt_checks = {
                    row[0]: row[1].casefold() for row in cursor.fetchall()
                }
                cursor.execute(
                    """
                    SELECT pg_get_constraintdef(oid)
                    FROM pg_constraint
                    WHERE conrelid = to_regclass(%s)
                    """,
                    (f"public.{prefix}_generations",),
                )
                generation_constraints = "\n".join(
                    row[0].casefold() for row in cursor.fetchall()
                )
                cursor.execute(
                    """
                    SELECT indexdef
                    FROM pg_indexes
                    WHERE schemaname = current_schema()
                      AND tablename = %s
                    """,
                    (f"{prefix}_generations",),
                )
                generation_indexes = "\n".join(
                    row[0].casefold() for row in cursor.fetchall()
                )
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
                        f"{prefix}_principal_memory_ledger_watermark",
                    ],),
                )
                local_rights_tables = {row[0] for row in cursor.fetchall()}

        assert first.applied is True
        assert second.applied is False
        assert first.migration_id == LATEST_RUNTIME_MIGRATION.migration_id
        assert any(
            spec.migration_id == "frontend_product_experience_v15"
            for spec in RUNTIME_MIGRATIONS
        )
        assert "heartbeat_at" in columns
        assert "lease_expires_at" in columns
        assert "source_decision_id" in generation_columns
        assert {
            "decision_prompt_version",
            "decision_prompt_sha256",
            "generation_prompt_version",
            "generation_prompt_sha256",
        } <= generation_columns
        assert {
            "decision_prompt_version",
            "decision_prompt_sha256",
        } <= decision_columns
        assert {
            "cached_input_tokens",
            "provider_response_id_sha256",
        } <= decision_attempt_columns
        metrics_check = runtime_schema_identifier(
            prefix, "decision_attempt_metrics_check"
        )
        trace_check = runtime_schema_identifier(
            prefix, "decision_attempt_usage_trace_check"
        )
        assert metrics_check in decision_attempt_checks
        assert {
            "duration_ms",
            "input_tokens",
            "output_tokens",
            "provider_invocations",
        } <= set(
            decision_attempt_checks[metrics_check]
            .replace("(", " ")
            .replace(")", " ")
            .replace(",", " ")
            .split()
        )
        assert trace_check in decision_attempt_checks
        assert "cached_input_tokens" in decision_attempt_checks[trace_check]
        assert "provider_response_id_sha256" in decision_attempt_checks[trace_check]
        assert "cached_input_tokens <= input_tokens" in decision_attempt_checks[
            trace_check
        ]
        assert "^[0-9a-f]{64}$" in decision_attempt_checks[trace_check]
        PostgresDecisionStore(
            dsn=postgres_dsn,
            table_prefix=prefix,
            schema_mode="validate",
        )
        with psycopg2.connect(postgres_dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL("ALTER TABLE {attempts} DROP CONSTRAINT {constraint}").format(
                        attempts=sql.Identifier(f"{prefix}_decision_attempts"),
                        constraint=sql.Identifier(trace_check),
                    )
                )
        with pytest.raises(PostgresSchemaNotReady, match="checks are incompatible"):
            PostgresDecisionStore(
                dsn=postgres_dsn,
                table_prefix=prefix,
                schema_mode="validate",
            )
        with psycopg2.connect(postgres_dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        "ALTER TABLE {attempts} ADD CONSTRAINT {constraint} "
                        "CHECK ((cached_input_tokens IS NULL OR cached_input_tokens >= 0) "
                        "AND (input_tokens IS NULL OR cached_input_tokens IS NULL "
                        "OR cached_input_tokens >= input_tokens) "
                        "AND (duration_ms IS NULL OR duration_ms < 999999) "
                        "AND (provider_response_id_sha256 IS NULL "
                        "OR provider_response_id_sha256 ~ '^[0-9a-f]{{64}}$'))"
                    ).format(
                        attempts=sql.Identifier(f"{prefix}_decision_attempts"),
                        constraint=sql.Identifier(trace_check),
                    )
                )
        with pytest.raises(PostgresSchemaNotReady, match="checks are incompatible"):
            PostgresDecisionStore(
                dsn=postgres_dsn,
                table_prefix=prefix,
                schema_mode="validate",
            )
        with psycopg2.connect(postgres_dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        "ALTER TABLE {attempts} DROP CONSTRAINT {constraint}"
                    ).format(
                        attempts=sql.Identifier(f"{prefix}_decision_attempts"),
                        constraint=sql.Identifier(trace_check),
                    )
                )
                cursor.execute(
                    sql.SQL(
                        "ALTER TABLE {attempts} ADD CONSTRAINT {constraint} "
                        "CHECK ((cached_input_tokens IS NULL OR cached_input_tokens >= 0) "
                        "AND (input_tokens IS NULL OR cached_input_tokens IS NULL "
                        "OR cached_input_tokens <= input_tokens) "
                        "AND (provider_response_id_sha256 IS NULL "
                        "OR provider_response_id_sha256 ~ '^[0-9a-f]+$') "
                        "AND (output_sha256 IS NULL "
                        "OR output_sha256 ~ '^[0-9a-f]{{64}}$'))"
                    ).format(
                        attempts=sql.Identifier(f"{prefix}_decision_attempts"),
                        constraint=sql.Identifier(trace_check),
                    )
                )
        with pytest.raises(PostgresSchemaNotReady, match="checks are incompatible"):
            PostgresDecisionStore(
                dsn=postgres_dsn,
                table_prefix=prefix,
                schema_mode="validate",
            )
        assert "foreign key (source_decision_id)" in generation_constraints
        assert f"{prefix}_followup_decisions" in generation_constraints
        assert "unique index" in generation_indexes
        assert "source_decision_id" in generation_indexes
        assert "where (source_decision_id is not null)" in generation_indexes
        assert local_rights_tables == {
            f"{prefix}_principal_memory_controls",
            f"{prefix}_principal_memory_exports",
            f"{prefix}_principal_memory_tombs",
            f"{prefix}_principal_memory_refs",
            f"{prefix}_principal_memory_ledger_watermark",
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
    postgres_dsn, monkeypatch, tmp_path
):
    import psycopg2
    from psycopg2 import sql
    from app.services import runtime

    prefix = make_runtime_table_prefix("principal_rights_upgrade")
    vector = make_runtime_table_prefix("principal_rights_vector")
    migrations_table = f"{prefix}_schema_migrations"
    v10 = next(
        migration
        for migration in RUNTIME_MIGRATIONS
        if migration.migration_id == "report_job_heartbeat_v1"
    )
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
        assert result.migration_id == LATEST_RUNTIME_MIGRATION.migration_id

        runtime.reset_runtime_for_tests()
        monkeypatch.setenv("POSTGRES_DSN", postgres_dsn)
        monkeypatch.setenv("INTERVIEW_RUNTIME_STORE", "postgres")
        monkeypatch.setenv("INTERVIEW_RUNTIME_TABLE_PREFIX", prefix)
        for name, value in {
            "MEMORY_LONG_TERM_MODE": "local_consume",
            "MEMORY_LOCAL_PRINCIPAL_ENABLED": "true",
            "MEMORY_TRUSTED_LOCAL_PRINCIPAL_MEMORY_API_ENABLED": "true",
            "MEMORY_LONG_TERM_WRITE_SHADOW_ENABLED": "true",
            "MEMORY_LONG_TERM_READ_SHADOW_ENABLED": "true",
            "MEMORY_LONG_TERM_LOCAL_CONSUMPTION_ENABLED": "true",
            "MEMORY_PRINCIPAL_TOMBSTONE_LEDGER_PATH": str(
                (tmp_path / "operator-ledger.jsonl").resolve()
            ),
        }.items():
            monkeypatch.setenv(name, value)
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


@pytest.mark.pg_runtime
def test_dirty_exclusive_facts_block_migration_until_explicit_resolution(
    postgres_dsn,
):
    import psycopg2
    from psycopg2 import sql

    prefix = make_runtime_table_prefix("exclusive_dirty")
    vector = make_runtime_table_prefix("exclusive_dirty_vector")
    store = PostgresPrincipalMemoryFactStore(
        dsn=postgres_dsn,
        table_prefix=prefix,
        schema_mode="migrate",
    )
    migrations_table = f"{prefix}_schema_migrations"
    exclusive_index = runtime_schema_identifier(
        prefix,
        "principal_memory_facts_active_exclusive_uq",
    )
    previous = next(
        migration
        for migration in RUNTIME_MIGRATIONS
        if migration.migration_id == "principal_memory_integrity_v2"
    )
    facts = [
        make_active_language("en", "1"),
        make_active_language("mixed", "2"),
    ]
    resolution = make_active_language("zh_hans", "3")
    try:
        with psycopg2.connect(postgres_dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL("DROP INDEX {index}").format(
                        index=sql.Identifier(exclusive_index)
                    )
                )
                for fact in facts:
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
                cursor.execute(
                    sql.SQL(
                        "CREATE TABLE {table} (migration_id TEXT PRIMARY KEY,"
                        "checksum TEXT NOT NULL,transaction_mode TEXT NOT NULL,"
                        "applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW())"
                    ).format(table=sql.Identifier(migrations_table))
                )
                cursor.execute(
                    sql.SQL(
                        "INSERT INTO {table} "
                        "(migration_id,checksum,transaction_mode) VALUES (%s,%s,%s)"
                    ).format(table=sql.Identifier(migrations_table)),
                    (
                        previous.migration_id,
                        previous.checksum,
                        previous.transaction_mode,
                    ),
                )

        with pytest.raises(PostgresMigrationConflict, match="explicit resolution"):
            migrate_postgres_runtime(
                dsn=postgres_dsn,
                table_prefix=prefix,
                pgvector_table=vector,
                embedding_provider=DisabledEmbeddingProvider(
                    model_name="disabled",
                    dimension=3,
                ),
                run_checkpointer_setup=False,
            )
        before_resolution = store.list_by_principal(
            deployment_id="single-tenant-local",
            principal_id="local-owner",
            limit=10,
            include_terminal=True,
        )
        assert [fact.status for fact in before_resolution].count("active") == 2

        store.declare_active(
            resolution,
            exclusive_key="interview_language",
            now=NOW,
        )
        result = migrate_postgres_runtime(
            dsn=postgres_dsn,
            table_prefix=prefix,
            pgvector_table=vector,
            embedding_provider=DisabledEmbeddingProvider(
                model_name="disabled",
                dimension=3,
            ),
            run_checkpointer_setup=False,
        )

        assert result.migration_id == LATEST_RUNTIME_MIGRATION.migration_id
        stored = store.list_by_principal(
            deployment_id="single-tenant-local",
            principal_id="local-owner",
            limit=10,
            include_terminal=True,
        )
        assert [fact.status for fact in stored].count("active") == 1
        with psycopg2.connect(postgres_dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT COUNT(*) FROM pg_indexes WHERE schemaname='public' "
                    "AND tablename=%s AND indexname=%s",
                    (store.table, exclusive_index),
                )
                assert cursor.fetchone()[0] == 1
    finally:
        with psycopg2.connect(postgres_dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema='public' "
                    "AND (table_name LIKE %s OR table_name LIKE %s)",
                    (prefix + "_%", vector + "_%"),
                )
                for (name,) in cursor.fetchall():
                    cursor.execute(
                        sql.SQL("DROP TABLE IF EXISTS {table} CASCADE").format(
                            table=sql.Identifier(name)
                        )
                    )


@pytest.mark.pg_runtime
def test_partial_taxonomy_backfill_is_repaired_idempotently(postgres_dsn):
    import psycopg2
    from psycopg2 import sql

    prefix = make_runtime_table_prefix("exclusive_partial")
    vector = make_runtime_table_prefix("exclusive_partial_vector")
    store = PostgresPrincipalMemoryFactStore(
        dsn=postgres_dsn,
        table_prefix=prefix,
        schema_mode="migrate",
    )
    migrations_table = f"{prefix}_schema_migrations"
    previous = next(
        migration
        for migration in RUNTIME_MIGRATIONS
        if migration.migration_id == "principal_memory_integrity_v2"
    )
    proposal = make_active_language("en", "4").model_copy(
        update={
            "authority": "model_proposed",
            "status": "proposed",
            "user_confirmed": False,
            "confirmed_at": None,
            "expires_at": None,
        }
    )
    try:
        store.create_proposal(proposal)
        with psycopg2.connect(postgres_dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        "ALTER TABLE {table} ALTER COLUMN taxonomy_key DROP NOT NULL"
                    ).format(table=sql.Identifier(store.table))
                )
                cursor.execute(
                    sql.SQL(
                        "UPDATE {table} SET taxonomy_key=NULL,exclusive_scope_key=NULL"
                    ).format(table=sql.Identifier(store.table))
                )
                cursor.execute(
                    sql.SQL(
                        "CREATE TABLE {table} (migration_id TEXT PRIMARY KEY,"
                        "checksum TEXT NOT NULL,transaction_mode TEXT NOT NULL,"
                        "applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW())"
                    ).format(table=sql.Identifier(migrations_table))
                )
                cursor.execute(
                    sql.SQL(
                        "INSERT INTO {table} "
                        "(migration_id,checksum,transaction_mode) VALUES (%s,%s,%s)"
                    ).format(table=sql.Identifier(migrations_table)),
                    (
                        previous.migration_id,
                        previous.checksum,
                        previous.transaction_mode,
                    ),
                )

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
        assert first.applied is True
        assert second.applied is False
        with psycopg2.connect(postgres_dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        "SELECT taxonomy_key,exclusive_scope_key "
                        "FROM {table} WHERE fact_id=%s"
                    ).format(table=sql.Identifier(store.table)),
                    (proposal.fact_id,),
                )
                assert cursor.fetchone() == (
                    "interview_language",
                    "interview_language",
                )
                cursor.execute(
                    "SELECT is_nullable FROM information_schema.columns "
                    "WHERE table_schema='public' AND table_name=%s "
                    "AND column_name='taxonomy_key'",
                    (store.table,),
                )
                assert cursor.fetchone()[0] == "NO"
    finally:
        with psycopg2.connect(postgres_dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema='public' "
                    "AND (table_name LIKE %s OR table_name LIKE %s)",
                    (prefix + "_%", vector + "_%"),
                )
                for (name,) in cursor.fetchall():
                    cursor.execute(
                        sql.SQL("DROP TABLE IF EXISTS {table} CASCADE").format(
                            table=sql.Identifier(name)
                        )
                    )
