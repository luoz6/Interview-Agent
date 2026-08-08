from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator

from app.services.config import derive_pgvector_table_names
from app.services.context_artifact_store import PostgresContextArtifactStore
from app.services.interview_generation_store import PostgresInterviewGenerationStore
from app.services.interview_workflow_store import PostgresInterviewWorkflowStore
from app.services.postgres_identifiers import (
    derive_runtime_identifiers,
    runtime_schema_identifier,
    validate_runtime_table_prefix,
)
from app.services.postgres_session import PostgresInterviewSessionStore
from app.services.report_jobs import PostgresReportJobStore
from app.services.review_workflow_store import PostgresReviewWorkflowStore
from app.services.runtime_signal_metrics import PostgresRuntimeSignalStore
from app.services.postgres_memory_metrics import PostgresMemoryMetricStore
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
from app.services.postgres_principal_memory_ledger import (
    PostgresPrincipalMemoryLedgerWatermarkStore,
)
from app.services.vector_store import PgVectorKnowledgeStore
from app.services.postgres_schema_contract import (
    LATEST_RUNTIME_MIGRATION,
    RUNTIME_MIGRATIONS,
    RUNTIME_SCHEMA_V16_MANIFEST,
)
from app.services.workflow_thread_lock import advisory_lock_key


RUNTIME_MIGRATION_ID = LATEST_RUNTIME_MIGRATION.migration_id
RUNTIME_MIGRATION_MANIFEST = RUNTIME_SCHEMA_V16_MANIFEST
RUNTIME_MIGRATION_CHECKSUM = LATEST_RUNTIME_MIGRATION.checksum


class PostgresMigrationConflict(RuntimeError):
    pass


@dataclass(frozen=True)
class PostgresMigrationResult:
    migration_id: str
    checksum: str
    applied: bool
    runtime_identifier_max_bytes: int


class BorrowedMigrationConnectionProvider:
    """Yield one operator-owned transaction connection without closing it."""

    def __init__(self, connection: Any) -> None:
        self.connection_object = connection

    @contextmanager
    def connection(self) -> Iterator[Any]:
        yield self.connection_object


def migrate_postgres_runtime(
    *,
    dsn: str,
    table_prefix: str,
    pgvector_table: str,
    embedding_provider: Any,
    connect=None,
    run_checkpointer_setup: bool = True,
) -> PostgresMigrationResult:
    validate_runtime_table_prefix(table_prefix)
    registry = derive_runtime_identifiers(table_prefix)
    versions_table, releases_table = derive_pgvector_table_names(pgvector_table)
    if connect is None:
        import psycopg2

        connect = psycopg2.connect
    connection = connect(dsn)
    lock_key = advisory_lock_key(f"runtime-migration:{table_prefix}")
    migrations_table = f"{table_prefix}_schema_migrations"
    applied = False
    locked = False
    try:
        connection.autocommit = False
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_schema()")
            schema_row = cursor.fetchone()
            if schema_row is None or schema_row[0] != "public":
                raise PostgresMigrationConflict(
                    "PostgreSQL runtime migration requires the public schema"
                )
            cursor.execute("SELECT pg_advisory_lock(%s)", (lock_key,))
            locked = True
            from psycopg2 import sql

            cursor.execute(
                sql.SQL(
                    """
                    CREATE TABLE IF NOT EXISTS {migrations} (
                        migration_id TEXT PRIMARY KEY,
                        checksum TEXT NOT NULL,
                        transaction_mode TEXT NOT NULL,
                        applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                ).format(migrations=sql.Identifier(migrations_table))
            )
            cursor.execute(
                sql.SQL(
                    "SELECT checksum FROM {migrations} WHERE migration_id = %s"
                ).format(migrations=sql.Identifier(migrations_table)),
                (RUNTIME_MIGRATION_ID,),
            )
            row = cursor.fetchone()
            cursor.execute(
                sql.SQL(
                    "SELECT migration_id, checksum, transaction_mode "
                    "FROM {migrations} WHERE migration_id = ANY(%s::text[])"
                ).format(migrations=sql.Identifier(migrations_table)),
                ([spec.migration_id for spec in RUNTIME_MIGRATIONS],),
            )
            applied_contracts = {
                migration_id: (checksum, transaction_mode)
                for migration_id, checksum, transaction_mode in cursor.fetchall()
            }
            for spec in RUNTIME_MIGRATIONS:
                applied_contract = applied_contracts.get(spec.migration_id)
                if applied_contract is not None and applied_contract != (
                    spec.checksum,
                    spec.transaction_mode,
                ):
                    raise PostgresMigrationConflict(
                        "applied PostgreSQL runtime migration checksum diverged"
                    )
        if row is not None and row[0] != RUNTIME_MIGRATION_CHECKSUM:
            raise PostgresMigrationConflict(
                "applied PostgreSQL runtime migration checksum diverged"
            )

        if row is None:
            provider = BorrowedMigrationConnectionProvider(connection)
            PostgresInterviewSessionStore(
                dsn=dsn,
                connection_provider=provider,
                agent_run_connection_provider=provider,
                table_prefix=table_prefix,
                schema_mode="migrate",
            )
            PostgresInterviewGenerationStore(
                dsn=dsn,
                connection_provider=provider,
                table_prefix=table_prefix,
                schema_mode="migrate",
            )
            PostgresInterviewWorkflowStore(
                dsn=dsn,
                connection_provider=provider,
                table_prefix=table_prefix,
                schema_mode="migrate",
            )
            PostgresReportJobStore(
                dsn=dsn,
                connection_provider=provider,
                table_prefix=table_prefix,
                schema_mode="migrate",
            )
            PostgresReviewWorkflowStore(
                dsn=dsn,
                connection_provider=provider,
                table_prefix=table_prefix,
                schema_mode="migrate",
            )
            PostgresRuntimeSignalStore(
                dsn=dsn,
                connection_provider=provider,
                table_prefix=table_prefix,
                schema_mode="migrate",
            )
            PostgresContextArtifactStore(
                dsn=dsn,
                connection_provider=provider,
                table_prefix=table_prefix,
                schema_mode="migrate",
            )
            _upgrade_context_artifact_identity_v1(
                connection,
                table_prefix=table_prefix,
            )
            from app.services.postgres_question_memory_index import (
                PostgresQuestionMemoryIndexStore,
            )

            PostgresQuestionMemoryIndexStore(
                dsn=dsn,
                connection_provider=provider,
                table_prefix=table_prefix,
                schema_mode="migrate",
            )
            from app.services.postgres_session_deletion import (
                PostgresSessionDeletionJobStore,
            )

            PostgresSessionDeletionJobStore(
                dsn=dsn,
                connection_provider=provider,
                table_prefix=table_prefix,
                schema_mode="migrate",
            )
            from app.services.postgres_session_deletion_tombstones import (
                PostgresSessionDeletionTombstoneStore,
            )

            PostgresSessionDeletionTombstoneStore(
                dsn=dsn,
                connection_provider=provider,
                table_prefix=table_prefix,
                schema_mode="migrate",
            )
            PostgresMemoryMetricStore(
                dsn=dsn,
                connection_provider=provider,
                table_prefix=table_prefix,
                schema_mode="migrate",
            )
            PostgresPrincipalMemoryConsentStore(
                dsn=dsn,
                connection_provider=provider,
                table_prefix=table_prefix,
                schema_mode="migrate",
            )
            _upgrade_principal_memory_exclusive_scope(
                connection,
                table_prefix=table_prefix,
            )
            PostgresPrincipalMemoryFactStore(
                dsn=dsn,
                connection_provider=provider,
                table_prefix=table_prefix,
                schema_mode="migrate",
            )
            PostgresPrincipalMemoryControlStore(
                dsn=dsn,
                connection_provider=provider,
                table_prefix=table_prefix,
                schema_mode="migrate",
            )
            PostgresPrincipalMemoryExportStore(
                dsn=dsn,
                connection_provider=provider,
                table_prefix=table_prefix,
                schema_mode="migrate",
            )
            PostgresPrincipalMemoryDeletionTombstoneStore(
                dsn=dsn,
                connection_provider=provider,
                table_prefix=table_prefix,
                schema_mode="migrate",
            )
            PostgresPrincipalMemorySafeRefStore(
                dsn=dsn,
                connection_provider=provider,
                table_prefix=table_prefix,
                schema_mode="migrate",
            )
            PostgresPrincipalMemoryLedgerWatermarkStore(
                dsn=dsn,
                connection_provider=provider,
                table_prefix=table_prefix,
                schema_mode="migrate",
            )
            from app.services.postgres_draft_store import PostgresDraftStore
            from app.services.postgres_prep_plan_store import PostgresPrepPlanStore
            from app.services.postgres_interview_launch_repository import (
                PostgresInterviewLaunchRepository,
            )

            PostgresDraftStore(
                dsn=dsn,
                connection_provider=provider,
                table_prefix=table_prefix,
                schema_mode="migrate",
            )
            PostgresPrepPlanStore(
                dsn=dsn,
                connection_provider=provider,
                table_prefix=table_prefix,
                schema_mode="migrate",
            )
            PostgresInterviewLaunchRepository(
                dsn=dsn,
                connection_provider=provider,
                table_prefix=table_prefix,
                schema_mode="migrate",
            )
            _upgrade_interview_workflow_engine_constraint(
                connection,
                table_prefix=table_prefix,
            )
            _upgrade_interview_memory_policy_constraint(
                connection,
                table_prefix=table_prefix,
            )
            vector_store = PgVectorKnowledgeStore(
                dsn=dsn,
                connection_provider=provider,
                table_name=pgvector_table,
                embedding_provider=embedding_provider,
                schema_mode="migrate",
            )
            vector_store.ensure_schema()
            connection.commit()

            if run_checkpointer_setup:
                _setup_langgraph_checkpointer(dsn)

            with connection.cursor() as cursor:
                from psycopg2 import sql

                cursor.execute(
                    sql.SQL(
                        """
                        INSERT INTO {migrations} (
                            migration_id, checksum, transaction_mode
                        ) VALUES (%s, %s, %s)
                        ON CONFLICT (migration_id) DO NOTHING
                        """
                    ).format(migrations=sql.Identifier(migrations_table)),
                    (
                        RUNTIME_MIGRATION_ID,
                        RUNTIME_MIGRATION_CHECKSUM,
                        LATEST_RUNTIME_MIGRATION.transaction_mode,
                    ),
                )
            connection.commit()
            applied = True
        elif run_checkpointer_setup:
            # setup() is idempotent and remains migration-owned. This repairs
            # a process loss after application DDL commit but before Saver DDL.
            _setup_langgraph_checkpointer(dsn)

        return PostgresMigrationResult(
            migration_id=RUNTIME_MIGRATION_ID,
            checksum=RUNTIME_MIGRATION_CHECKSUM,
            applied=applied,
            runtime_identifier_max_bytes=registry.longest_byte_length,
        )
    except BaseException:
        try:
            connection.rollback()
        except Exception:
            pass
        raise
    finally:
        if locked and not getattr(connection, "closed", True):
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_advisory_unlock(%s)", (lock_key,))
                    row = cursor.fetchone()
                    if not row or not bool(row[0]):
                        connection.close()
                if not getattr(connection, "closed", True):
                    connection.commit()
            except Exception:
                try:
                    connection.close()
                except Exception:
                    pass
        if not getattr(connection, "closed", True):
            connection.close()


def _setup_langgraph_checkpointer(dsn: str) -> None:
    from langgraph.checkpoint.postgres import PostgresSaver

    with PostgresSaver.from_conn_string(dsn) as saver:
        saver.setup()


def _upgrade_context_artifact_identity_v1(
    connection: Any,
    *,
    table_prefix: str,
) -> None:
    """Add nullable v1 identity material without rewriting legacy v0 rows."""

    from psycopg2 import sql

    table = f"{table_prefix}_context_artifacts"
    constraint_name = runtime_schema_identifier(
        table_prefix,
        "context_artifacts_identity_v1_check",
    )
    with connection.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                "ALTER TABLE {table} "
                "ADD COLUMN IF NOT EXISTS identity_schema_version TEXT, "
                "ADD COLUMN IF NOT EXISTS compression_intent_sha256 TEXT"
            ).format(table=sql.Identifier(table))
        )
        cursor.execute(
            "SELECT 1 FROM pg_constraint "
            "WHERE conname=%s AND conrelid=to_regclass(%s)",
            (constraint_name, f"public.{table}"),
        )
        if cursor.fetchone() is None:
            cursor.execute(
                sql.SQL(
                    "ALTER TABLE {table} ADD CONSTRAINT {constraint} CHECK ("
                    "(identity_schema_version IS NULL AND "
                    "compression_intent_sha256 IS NULL) OR "
                    "(identity_schema_version IS NOT NULL AND "
                    "identity_schema_version = 'identity-v1' AND "
                    "compression_intent_sha256 IS NOT NULL AND "
                    "compression_intent_sha256 ~ '^[0-9a-f]{{64}}$'))"
                ).format(
                    table=sql.Identifier(table),
                    constraint=sql.Identifier(constraint_name),
                )
            )


def _upgrade_principal_memory_exclusive_scope(
    connection: Any,
    *,
    table_prefix: str,
) -> None:
    """Install database-owned taxonomy keys without choosing fact winners."""

    from psycopg2 import sql

    from app.services.principal_memory_contracts import (
        derive_principal_fact_taxonomy_keys,
    )
    from app.services.principal_memory_exclusive_scan import (
        scan_exclusive_facts,
    )

    table = f"{table_prefix}_principal_memory_facts"
    constraint_name = runtime_schema_identifier(
        table_prefix,
        "principal_memory_facts_taxonomy_scope_check",
    )
    exclusive_index_name = runtime_schema_identifier(
        table_prefix,
        "principal_memory_facts_active_exclusive_uq",
    )
    with connection.cursor() as cursor:
        cursor.execute("SELECT to_regclass(%s)", (f"public.{table}",))
        relation = cursor.fetchone()
        if relation is None or relation[0] is None:
            return
        cursor.execute(
            sql.SQL(
                "SELECT fact_id,deployment_id,principal_id,fact_type,"
                "normalized_fact,status,supersedes_fact_id FROM {}"
            ).format(sql.Identifier(table))
        )
        names = (
            "fact_id",
            "deployment_id",
            "principal_id",
            "fact_type",
            "normalized_fact",
            "status",
            "supersedes_fact_id",
        )
        rows = [dict(zip(names, row)) for row in cursor.fetchall()]
        report = scan_exclusive_facts(rows)
        if report.repair_required:
            raise PostgresMigrationConflict(
                "principal memory exclusive facts require explicit resolution"
            )
        derived: list[tuple[str, str | None, str, str, str]] = []
        try:
            for row in rows:
                taxonomy_key, exclusive_scope_key = (
                    derive_principal_fact_taxonomy_keys(
                        fact_type=row["fact_type"],
                        normalized_fact=row["normalized_fact"],
                    )
                )
                derived.append(
                    (
                        taxonomy_key,
                        exclusive_scope_key,
                        row["deployment_id"],
                        row["principal_id"],
                        row["fact_id"],
                    )
                )
        except (TypeError, ValueError) as exc:
            raise PostgresMigrationConflict(
                "principal memory taxonomy backfill requires explicit repair"
            ) from exc
        cursor.execute(
            sql.SQL(
                "ALTER TABLE {} ADD COLUMN IF NOT EXISTS taxonomy_key TEXT, "
                "ADD COLUMN IF NOT EXISTS exclusive_scope_key TEXT"
            ).format(sql.Identifier(table))
        )
        for values in derived:
            cursor.execute(
                sql.SQL(
                    "UPDATE {} SET taxonomy_key=%s,exclusive_scope_key=%s "
                    "WHERE deployment_id=%s AND principal_id=%s AND fact_id=%s"
                ).format(sql.Identifier(table)),
                values,
            )
            if cursor.rowcount != 1:
                raise PostgresMigrationConflict(
                    "principal memory taxonomy backfill changed concurrently"
                )
        cursor.execute(
            sql.SQL("ALTER TABLE {} ALTER COLUMN taxonomy_key SET NOT NULL").format(
                sql.Identifier(table)
            )
        )
        cursor.execute(
            "SELECT 1 FROM pg_constraint "
            "WHERE conname=%s AND conrelid=to_regclass(%s)",
            (constraint_name, f"public.{table}"),
        )
        if cursor.fetchone() is None:
            cursor.execute(
                sql.SQL(
                    "ALTER TABLE {table} ADD CONSTRAINT {constraint} CHECK ("
                    "(taxonomy_key IN ('interview_language','target_role_family',"
                    "'accessibility_preference') AND "
                    "exclusive_scope_key IS NOT NULL AND "
                    "exclusive_scope_key=taxonomy_key) OR "
                    "(taxonomy_key NOT IN ('interview_language',"
                    "'target_role_family','accessibility_preference') AND "
                    "exclusive_scope_key IS NULL))"
                ).format(
                    table=sql.Identifier(table),
                    constraint=sql.Identifier(constraint_name),
                )
            )
        cursor.execute(
            sql.SQL(
                "CREATE UNIQUE INDEX IF NOT EXISTS {index} ON {table} "
                "(deployment_id,principal_id,exclusive_scope_key) "
                "WHERE status='active' AND exclusive_scope_key IS NOT NULL"
            ).format(
                index=sql.Identifier(exclusive_index_name),
                table=sql.Identifier(table),
            )
        )


def _upgrade_interview_workflow_engine_constraint(
    connection: Any,
    *,
    table_prefix: str,
) -> None:
    """Replace the v1-only CHECK without rewriting existing business rows."""

    from psycopg2 import sql

    sessions_table = f"{table_prefix}_sessions"
    constraint_name = runtime_schema_identifier(
        table_prefix, "sessions_workflow_engine_check"
    )
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT c.conname
            FROM pg_constraint c
            WHERE c.conrelid = to_regclass(%s)
              AND c.contype = 'c'
              AND pg_get_constraintdef(c.oid) ILIKE '%%workflow_engine%%'
            """,
            (f"public.{sessions_table}",),
        )
        for (existing_name,) in cursor.fetchall():
            cursor.execute(
                sql.SQL(
                    "ALTER TABLE {sessions} DROP CONSTRAINT {constraint}"
                ).format(
                    sessions=sql.Identifier(sessions_table),
                    constraint=sql.Identifier(existing_name),
                )
            )
        cursor.execute(
            sql.SQL(
                "ALTER TABLE {sessions} ADD CONSTRAINT {constraint} "
                "CHECK (workflow_engine IN "
                "('legacy', 'langgraph-v1', 'langgraph-v2'))"
            ).format(
                sessions=sql.Identifier(sessions_table),
                constraint=sql.Identifier(constraint_name),
            )
        )


def _upgrade_interview_memory_policy_constraint(
    connection: Any,
    *,
    table_prefix: str,
) -> None:
    from psycopg2 import sql

    sessions_table = f"{table_prefix}_sessions"
    constraint_name = runtime_schema_identifier(
        table_prefix,
        "sessions_memory_policy_check",
    )
    with connection.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                "ALTER TABLE {sessions} ADD COLUMN IF NOT EXISTS memory_policy_version TEXT"
            ).format(sessions=sql.Identifier(sessions_table))
        )
        cursor.execute(
            sql.SQL(
                "UPDATE {sessions} SET memory_policy_version = CASE "
                "WHEN workflow_engine = 'langgraph-v2' "
                "THEN 'question-conversation-v1' "
                "ELSE 'deterministic-v1' END "
                "WHERE memory_policy_version IS NULL"
            ).format(sessions=sql.Identifier(sessions_table))
        )
        cursor.execute(
            """
            SELECT c.conname
            FROM pg_constraint c
            WHERE c.conrelid = to_regclass(%s)
              AND c.contype = 'c'
              AND pg_get_constraintdef(c.oid) ILIKE '%%memory_policy_version%%'
            """,
            (f"public.{sessions_table}",),
        )
        for (existing_name,) in cursor.fetchall():
            cursor.execute(
                sql.SQL(
                    "ALTER TABLE {sessions} DROP CONSTRAINT {constraint}"
                ).format(
                    sessions=sql.Identifier(sessions_table),
                    constraint=sql.Identifier(existing_name),
                )
            )
        cursor.execute(
            sql.SQL(
                "ALTER TABLE {sessions} ADD CONSTRAINT {constraint} "
                "CHECK (memory_policy_version IN ("
                "'deterministic-v1', 'question-conversation-v1', "
                "'question-memory-v1'))"
            ).format(
                sessions=sql.Identifier(sessions_table),
                constraint=sql.Identifier(constraint_name),
            )
        )
        cursor.execute(
            sql.SQL(
                "ALTER TABLE {sessions} ALTER COLUMN memory_policy_version SET NOT NULL"
            ).format(sessions=sql.Identifier(sessions_table))
        )
        cursor.execute(
            sql.SQL(
                "ALTER TABLE {sessions} ALTER COLUMN memory_policy_version SET DEFAULT 'deterministic-v1'"
            ).format(sessions=sql.Identifier(sessions_table))
        )
