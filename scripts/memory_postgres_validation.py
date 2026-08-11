from __future__ import annotations

import argparse
from dataclasses import dataclass
import os
from uuid import uuid4

from app.adapters.postgres.context_artifacts import PostgresContextArtifactStore
from app.services.embedding_providers import DisabledEmbeddingProvider
from app.services.postgres_connections import DirectPsycopg2ConnectionProvider
from app.services.postgres_question_memory_index import (
    PostgresQuestionMemoryIndexStore,
)
from app.services.postgres_runtime_migrations import migrate_postgres_runtime
from app.services.postgres_schema_contract import RUNTIME_MIGRATIONS
from app.services.postgres_session import PostgresInterviewSessionStore
from app.services.postgres_session_deletion import (
    PostgresSessionDeletionJobStore,
)
from app.services.postgres_session_deletion_tombstones import (
    PostgresSessionDeletionTombstoneStore,
)
from app.services.postgres_memory_metrics import PostgresMemoryMetricStore
from app.adapters.postgres.principal_memory import PostgresPrincipalMemoryFactStore
from app.services.postgres_principal_memory_consent import (
    PostgresPrincipalMemoryConsentStore,
)
from app.ports.postgres_scope import SAFE_POSTGRES_SCOPE_PREFIX
from scripts.memory_shadow_evidence_support import approved_postgres_scope


@dataclass(frozen=True)
class MemoryPostgresValidationResult:
    table_prefix: str
    migration_id: str
    required_migration_ids: tuple[str, ...]
    relation_count: int


def make_validation_prefix() -> str:
    return f"test_memval_{uuid4().hex[:12]}"


def assert_safe_prefix(prefix: str) -> None:
    if SAFE_POSTGRES_SCOPE_PREFIX.fullmatch(prefix) is None:
        raise ValueError("refusing to operate on a non-isolated memory prefix")


def _validate_runtime_stores(dsn: str, table_prefix: str) -> None:
    provider = DirectPsycopg2ConnectionProvider(
        dsn,
        connect_kwargs={"connect_timeout": 3},
    )
    PostgresInterviewSessionStore(
        dsn=dsn,
        connection_provider=provider,
        agent_run_connection_provider=provider,
        table_prefix=table_prefix,
        schema_mode="validate",
    )
    PostgresContextArtifactStore(
        dsn=dsn,
        connection_provider=provider,
        table_prefix=table_prefix,
        schema_mode="validate",
    )
    PostgresQuestionMemoryIndexStore(
        dsn=dsn,
        connection_provider=provider,
        table_prefix=table_prefix,
        schema_mode="validate",
    )
    PostgresSessionDeletionJobStore(
        dsn=dsn,
        connection_provider=provider,
        table_prefix=table_prefix,
        schema_mode="validate",
    )
    PostgresSessionDeletionTombstoneStore(
        dsn=dsn,
        connection_provider=provider,
        table_prefix=table_prefix,
        schema_mode="validate",
    )
    PostgresMemoryMetricStore(
        dsn=dsn,
        connection_provider=provider,
        table_prefix=table_prefix,
        schema_mode="validate",
    )
    PostgresPrincipalMemoryConsentStore(
        dsn=dsn,
        connection_provider=provider,
        table_prefix=table_prefix,
        schema_mode="validate",
    )
    PostgresPrincipalMemoryFactStore(
        dsn=dsn,
        connection_provider=provider,
        table_prefix=table_prefix,
        schema_mode="validate",
    )


def _relation_count(dsn: str, prefix: str) -> int:
    import psycopg2

    with psycopg2.connect(dsn, connect_timeout=3) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) FROM pg_tables "
                "WHERE schemaname = 'public' AND tablename LIKE %s",
                (f"{prefix}_%",),
            )
            row = cursor.fetchone()
    return int(row[0]) if row else 0


def run_validation(
    *,
    dsn: str,
    table_prefix: str,
) -> MemoryPostgresValidationResult:
    prefix = table_prefix
    assert_safe_prefix(prefix)
    vector_table = f"{prefix}_knowledge"
    result = migrate_postgres_runtime(
        dsn=dsn,
        table_prefix=prefix,
        pgvector_table=vector_table,
        embedding_provider=DisabledEmbeddingProvider(
            model_name="memory-validation-disabled",
            dimension=1024,
        ),
        run_checkpointer_setup=False,
    )
    _validate_runtime_stores(dsn, prefix)
    relation_count = _relation_count(dsn, prefix)
    return MemoryPostgresValidationResult(
        table_prefix=prefix,
        migration_id=result.migration_id,
        required_migration_ids=tuple(
            spec.migration_id for spec in RUNTIME_MIGRATIONS
        ),
        relation_count=relation_count,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate memory runtime stores on an isolated PostgreSQL prefix."
    )
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--table-prefix")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    prefix = args.table_prefix or make_validation_prefix()
    assert_safe_prefix(prefix)
    if not args.execute:
        print("mode=DRY_RUN")
        print(f"table_prefix={prefix}")
        print("dsn=REDACTED")
        return 0
    dsn = os.getenv("POSTGRES_DSN", "").strip()
    if not dsn:
        raise RuntimeError("POSTGRES_DSN is required")
    if not args.table_prefix:
        raise RuntimeError("--table-prefix is required for --execute")
    active = None
    with approved_postgres_scope(
        dsn=dsn,
        scope_prefix=args.table_prefix,
        environ=os.environ,
    ) as active:
        result = run_validation(dsn=dsn, table_prefix=args.table_prefix)
    if active is None or active.lease.cleanup_receipt is None:
        raise RuntimeError("memory validation cleanup receipt is missing")
    print("mode=EXECUTE")
    print(f"table_prefix={result.table_prefix}")
    print(f"migration_id={result.migration_id}")
    print(
        "required_migrations=" + ",".join(result.required_migration_ids)
    )
    print(f"relation_count={result.relation_count}")
    print("cleanup=verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
