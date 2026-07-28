from __future__ import annotations

from datetime import datetime
from typing import Literal, get_args

from app.services.postgres_connections import (
    ConnectionProvider,
    DirectPsycopg2ConnectionProvider,
)
from app.services.postgres_identifiers import validate_runtime_table_prefix
from app.services.postgres_schema import resolve_schema_mode, validate_relations


WorkflowSignalType = Literal["interview", "review", "shared"]
CanarySignalCode = Literal[
    "workflow_thread_busy",
    "workflow_thread_lock_lost",
    "generation_lease_lost",
    "fenced_write_rejected",
    "projection_conflict",
    "report_lease_lost",
    "review_effect_busy",
    "review_effect_conflict",
    "report_commit_conflict",
    "canary_signal_write_failed",
]

WORKFLOW_SIGNAL_TYPES = frozenset(get_args(WorkflowSignalType))
CANARY_SIGNAL_CODES = frozenset(get_args(CanarySignalCode))


def validate_workflow_signal_type(value: str) -> str:
    if value not in WORKFLOW_SIGNAL_TYPES:
        raise ValueError("unsupported workflow signal type")
    return value


def validate_canary_signal_code(value: str) -> str:
    if value not in CANARY_SIGNAL_CODES:
        raise ValueError("unsupported canary signal code")
    return value


def validate_observed_since(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("observed_since must be timezone-aware")
    return value


class NoopRuntimeSignalStore:
    def increment(self, *, workflow_type: str, signal_code: str) -> None:
        validate_workflow_signal_type(workflow_type)
        validate_canary_signal_code(signal_code)

    def sum_since(
        self,
        observed_since: datetime,
        *,
        workflow_type: str | None = None,
    ) -> dict[str, int]:
        validate_observed_since(observed_since)
        if workflow_type is not None:
            validate_workflow_signal_type(workflow_type)
        return {}

    def cleanup_older_than(self, *, hours: int) -> int:
        if hours < 1:
            raise ValueError("retention hours must be positive")
        return 0


class PostgresRuntimeSignalStore:
    def __init__(
        self,
        *,
        dsn: str | None = None,
        connection_provider: ConnectionProvider | None = None,
        table_prefix: str = "interview",
        schema_mode: str | None = None,
    ) -> None:
        validate_runtime_table_prefix(table_prefix)
        if connection_provider is None:
            if not dsn:
                raise ValueError("dsn or connection_provider is required")
            connection_provider = DirectPsycopg2ConnectionProvider(dsn)
            self._provider_is_owned = True
        else:
            self._provider_is_owned = False
        self.dsn = dsn or ""
        self._connection_provider = connection_provider
        self.table_prefix = table_prefix
        self.table = f"{table_prefix}_runtime_signal_buckets"
        self.schema_mode = resolve_schema_mode(
            schema_mode, provider_is_owned=self._provider_is_owned
        )
        if self.schema_mode == "migrate":
            self._ensure_schema()
        else:
            validate_relations(
                self._connection_provider,
                (self.table, f"{table_prefix}_schema_migrations"),
            )

    def increment(self, *, workflow_type: str, signal_code: str) -> None:
        workflow_type = validate_workflow_signal_type(workflow_type)
        signal_code = validate_canary_signal_code(signal_code)
        psycopg2, sql = self._import_psycopg2()
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        """
                        INSERT INTO {table} (
                            bucket_start, workflow_type, signal_code,
                            signal_count
                        )
                        VALUES (
                            date_trunc('minute', NOW()), %s, %s, 1
                        )
                        ON CONFLICT (
                            bucket_start, workflow_type, signal_code
                        ) DO UPDATE
                        SET signal_count = {table}.signal_count + 1,
                            updated_at = NOW()
                        """
                    ).format(table=sql.Identifier(self.table)),
                    (workflow_type, signal_code),
                )

    def sum_since(
        self,
        observed_since: datetime,
        *,
        workflow_type: str | None = None,
    ) -> dict[str, int]:
        observed_since = validate_observed_since(observed_since)
        params: list[object] = [observed_since]
        workflow_filter = ""
        if workflow_type is not None:
            workflow_type = validate_workflow_signal_type(workflow_type)
            workflow_filter = " AND workflow_type = %s"
            params.append(workflow_type)
        psycopg2, sql = self._import_psycopg2()
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        """
                        SELECT signal_code, SUM(signal_count)
                        FROM {table}
                        WHERE bucket_start >= %s
                        """
                        + workflow_filter
                        + " GROUP BY signal_code ORDER BY signal_code"
                    ).format(table=sql.Identifier(self.table)),
                    tuple(params),
                )
                rows = cursor.fetchall()
        return {str(code): int(count) for code, count in rows}

    def cleanup_older_than(self, *, hours: int) -> int:
        if hours < 1:
            raise ValueError("retention hours must be positive")
        psycopg2, sql = self._import_psycopg2()
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        """
                        DELETE FROM {table}
                        WHERE bucket_start <
                            NOW() - (%s * INTERVAL '1 hour')
                        """
                    ).format(table=sql.Identifier(self.table)),
                    (hours,),
                )
                return int(cursor.rowcount)

    def _ensure_schema(self) -> None:
        psycopg2, sql = self._import_psycopg2()
        workflow_values = sql.SQL(", ").join(
            sql.Literal(value) for value in sorted(WORKFLOW_SIGNAL_TYPES)
        )
        signal_values = sql.SQL(", ").join(
            sql.Literal(value) for value in sorted(CANARY_SIGNAL_CODES)
        )
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        """
                        CREATE TABLE IF NOT EXISTS {table} (
                            bucket_start TIMESTAMPTZ NOT NULL,
                            workflow_type TEXT NOT NULL
                                CHECK (workflow_type IN ({workflow_values})),
                            signal_code TEXT NOT NULL
                                CHECK (signal_code IN ({signal_values})),
                            signal_count BIGINT NOT NULL DEFAULT 0
                                CHECK (signal_count >= 0),
                            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            PRIMARY KEY (
                                bucket_start, workflow_type, signal_code
                            )
                        )
                        """
                    ).format(
                        table=sql.Identifier(self.table),
                        workflow_values=workflow_values,
                        signal_values=signal_values,
                    )
                )

    @staticmethod
    def _import_psycopg2():
        try:
            import psycopg2
            from psycopg2 import sql
        except ImportError as exc:
            raise RuntimeError("psycopg2-binary is required") from exc
        return psycopg2, sql
