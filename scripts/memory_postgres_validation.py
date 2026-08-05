from __future__ import annotations

import argparse
from dataclasses import dataclass
from hashlib import sha256
import os
import re
from typing import Any, Callable
from uuid import uuid4

from app.services.context_artifact_store import PostgresContextArtifactStore
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
from app.services.postgres_principal_memory import PostgresPrincipalMemoryFactStore
from app.services.postgres_principal_memory_consent import (
    PostgresPrincipalMemoryConsentStore,
)


SAFE_PREFIX = re.compile(r"^test_memval_[0-9a-f]{12}$")
PRODUCTION_DATABASE = re.compile(r"(?:^|[_-])(prod|production|live)(?:$|[_-])", re.I)


@dataclass(frozen=True)
class DatabaseFingerprint:
    digest: str
    database_name: str
    server_version_num: int


@dataclass(frozen=True)
class MemoryPostgresValidationResult:
    fingerprint: str
    table_prefix: str
    migration_id: str
    required_migration_ids: tuple[str, ...]
    relation_count: int
    cleaned: bool


def make_validation_prefix() -> str:
    return f"test_memval_{uuid4().hex[:12]}"


def assert_safe_prefix(prefix: str) -> None:
    if SAFE_PREFIX.fullmatch(prefix) is None:
        raise ValueError("refusing to operate on a non-isolated memory prefix")


def database_fingerprint(
    dsn: str,
    *,
    connect: Callable[..., Any] | None = None,
) -> DatabaseFingerprint:
    if connect is None:
        import psycopg2

        connect = psycopg2.connect
    connection = connect(dsn, connect_timeout=3)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT current_database(), current_setting('server_version_num')::int"
            )
            row = cursor.fetchone()
        if row is None:
            raise RuntimeError("PostgreSQL fingerprint query returned no row")
        database_name = str(row[0])
        server_version_num = int(row[1])
        if PRODUCTION_DATABASE.search(database_name):
            raise RuntimeError("refusing a production-like PostgreSQL database")
        digest = sha256(
            f"{database_name}|{server_version_num}".encode("utf-8")
        ).hexdigest()[:16]
        return DatabaseFingerprint(
            digest=digest,
            database_name=database_name,
            server_version_num=server_version_num,
        )
    finally:
        connection.close()


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


def cleanup_isolated_prefix(dsn: str, prefix: str) -> int:
    assert_safe_prefix(prefix)
    import psycopg2
    from psycopg2 import sql

    dropped = 0
    with psycopg2.connect(dsn, connect_timeout=3) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT tablename FROM pg_tables "
                "WHERE schemaname = 'public' AND tablename LIKE %s "
                "ORDER BY tablename",
                (f"{prefix}_%",),
            )
            tables = [str(row[0]) for row in cursor.fetchall()]
            if any(not table.startswith(f"{prefix}_") for table in tables):
                raise RuntimeError("isolated PostgreSQL cleanup escaped prefix")
            for table in reversed(tables):
                cursor.execute(
                    sql.SQL("DROP TABLE {table} CASCADE").format(
                        table=sql.Identifier(table)
                    )
                )
                dropped += 1
    return dropped


def run_validation(
    *,
    dsn: str,
    table_prefix: str | None = None,
    keep_tables: bool = False,
) -> MemoryPostgresValidationResult:
    prefix = table_prefix or make_validation_prefix()
    assert_safe_prefix(prefix)
    fingerprint = database_fingerprint(dsn)
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
    cleaned = False
    if not keep_tables:
        cleanup_isolated_prefix(dsn, prefix)
        cleaned = _relation_count(dsn, prefix) == 0
    return MemoryPostgresValidationResult(
        fingerprint=fingerprint.digest,
        table_prefix=prefix,
        migration_id=result.migration_id,
        required_migration_ids=tuple(
            spec.migration_id for spec in RUNTIME_MIGRATIONS
        ),
        relation_count=relation_count,
        cleaned=cleaned,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate memory runtime stores on an isolated PostgreSQL prefix."
    )
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--keep-tables", action="store_true")
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
    result = run_validation(
        dsn=dsn,
        table_prefix=prefix,
        keep_tables=args.keep_tables,
    )
    print("mode=EXECUTE")
    print(f"database_fingerprint={result.fingerprint}")
    print(f"table_prefix={result.table_prefix}")
    print(f"migration_id={result.migration_id}")
    print(
        "required_migrations=" + ",".join(result.required_migration_ids)
    )
    print(f"relation_count={result.relation_count}")
    print(f"cleanup={'verified' if result.cleaned else 'retained'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
