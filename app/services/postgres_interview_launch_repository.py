from __future__ import annotations

from typing import Any

from app.services.postgres_connections import (
    ConnectionProvider,
    DirectPsycopg2ConnectionProvider,
)
from app.services.postgres_identifiers import validate_postgres_identifier
from app.services.postgres_schema import resolve_schema_mode, validate_relations


class PostgresInterviewLaunchRepository:
    def __init__(
        self,
        dsn: str | None = None,
        *,
        connection_provider: ConnectionProvider | None = None,
        table_prefix: str = "interview",
        schema_mode: str | None = None,
    ) -> None:
        owned = connection_provider is None
        if connection_provider is None:
            if not dsn:
                raise ValueError("dsn or connection_provider is required")
            connection_provider = DirectPsycopg2ConnectionProvider(dsn)
        validate_postgres_identifier(table_prefix)
        self._provider = connection_provider
        self._commands_table = f"{table_prefix}_prep_plan_launch_commands"
        self._mappings_table = f"{table_prefix}_prep_plan_session_question_mappings"
        self._sessions_table = f"{table_prefix}_sessions"
        mode = resolve_schema_mode(schema_mode, provider_is_owned=owned)
        if mode == "migrate":
            self.ensure_schema()
        else:
            validate_relations(
                self._provider,
                (self._commands_table, self._mappings_table),
            )

    def ensure_schema(self) -> None:
        from psycopg2 import sql

        with self._provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        """
                        CREATE TABLE IF NOT EXISTS {commands} (
                            plan_id TEXT NOT NULL,
                            command_id TEXT NOT NULL,
                            consumed_plan_version INTEGER NOT NULL,
                            session_id TEXT NOT NULL,
                            bootstrap_status TEXT NOT NULL CHECK (
                                bootstrap_status IN ('bootstrap_pending', 'ready', 'failed_recoverable')
                            ),
                            bootstrap_attempt_count INTEGER NOT NULL DEFAULT 0,
                            last_bootstrap_attempt_at TIMESTAMPTZ NULL,
                            next_retry_at TIMESTAMPTZ NULL,
                            last_error_code TEXT NULL,
                            last_error_retryable BOOLEAN NOT NULL DEFAULT TRUE,
                            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            PRIMARY KEY (plan_id, command_id),
                            UNIQUE (plan_id),
                            FOREIGN KEY (session_id) REFERENCES {sessions}(session_id) ON DELETE CASCADE
                        )
                        """
                    ).format(
                        commands=sql.Identifier(self._commands_table),
                        sessions=sql.Identifier(self._sessions_table),
                    )
                )
                cursor.execute(
                    sql.SQL(
                        """
                        CREATE TABLE IF NOT EXISTS {mappings} (
                            session_id TEXT NOT NULL,
                            plan_question_id TEXT NOT NULL,
                            session_question_id TEXT NOT NULL,
                            position INTEGER NOT NULL CHECK (position >= 1),
                            kind TEXT NOT NULL CHECK (kind IN ('project', 'technical', 'system-design', 'behavioral')),
                            PRIMARY KEY (session_id, plan_question_id),
                            UNIQUE (session_id, session_question_id),
                            UNIQUE (session_id, position),
                            FOREIGN KEY (session_id) REFERENCES {sessions}(session_id) ON DELETE CASCADE
                        )
                        """
                    ).format(
                        mappings=sql.Identifier(self._mappings_table),
                        sessions=sql.Identifier(self._sessions_table),
                    )
                )
            if not hasattr(self._provider, "connection_object"):
                connection.commit()

    def get(self, plan_id: str, command_id: str) -> dict[str, Any] | None:
        with self._provider.connection() as connection:
            with connection.cursor() as cursor:
                return self.select_command(cursor, plan_id, command_id)

    def get_by_plan(self, plan_id: str) -> dict[str, Any] | None:
        with self._provider.connection() as connection:
            with connection.cursor() as cursor:
                return self.select_by_plan(cursor, plan_id)

    def mappings_for_session(self, session_id: str) -> list[dict[str, Any]]:
        from psycopg2 import sql

        with self._provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        "SELECT plan_question_id, session_question_id, position, kind "
                        "FROM {mappings} WHERE session_id=%s ORDER BY position"
                    ).format(mappings=sql.Identifier(self._mappings_table)),
                    (session_id,),
                )
                rows = cursor.fetchall()
        return [
            {
                "plan_question_id": row[0],
                "session_question_id": row[1],
                "position": row[2],
                "kind": row[3],
            }
            for row in rows
        ]

    def select_command(self, cursor, plan_id: str, command_id: str) -> dict[str, Any] | None:
        from psycopg2 import sql

        cursor.execute(
            sql.SQL(
                "SELECT plan_id, command_id, consumed_plan_version, session_id, "
                "bootstrap_status, bootstrap_attempt_count, last_bootstrap_attempt_at, "
                "next_retry_at, last_error_code, last_error_retryable, created_at, updated_at "
                "FROM {commands} WHERE plan_id=%s AND command_id=%s"
            ).format(commands=sql.Identifier(self._commands_table)),
            (plan_id, command_id),
        )
        return self._row(cursor.fetchone())

    def select_by_plan(self, cursor, plan_id: str) -> dict[str, Any] | None:
        from psycopg2 import sql

        cursor.execute(
            sql.SQL(
                "SELECT plan_id, command_id, consumed_plan_version, session_id, "
                "bootstrap_status, bootstrap_attempt_count, last_bootstrap_attempt_at, "
                "next_retry_at, last_error_code, last_error_retryable, created_at, updated_at "
                "FROM {commands} WHERE plan_id=%s"
            ).format(commands=sql.Identifier(self._commands_table)),
            (plan_id,),
        )
        return self._row(cursor.fetchone())

    def insert_pending(
        self,
        cursor,
        *,
        plan_id: str,
        command_id: str,
        consumed_plan_version: int,
        session_id: str,
        mappings: list[dict[str, Any]],
    ) -> dict[str, Any]:
        from psycopg2 import sql

        cursor.execute(
            sql.SQL(
                "INSERT INTO {commands} (plan_id, command_id, consumed_plan_version, "
                "session_id, bootstrap_status) VALUES (%s, %s, %s, %s, 'bootstrap_pending')"
            ).format(commands=sql.Identifier(self._commands_table)),
            (plan_id, command_id, consumed_plan_version, session_id),
        )
        for mapping in mappings:
            cursor.execute(
                sql.SQL(
                    "INSERT INTO {mappings} (session_id, plan_question_id, "
                    "session_question_id, position, kind) VALUES (%s, %s, %s, %s, %s)"
                ).format(mappings=sql.Identifier(self._mappings_table)),
                (
                    session_id,
                    mapping["plan_question_id"],
                    mapping["session_question_id"],
                    mapping["position"],
                    mapping["kind"],
                ),
            )
        return {
            "plan_id": plan_id,
            "command_id": command_id,
            "consumed_plan_version": consumed_plan_version,
            "session_id": session_id,
            "bootstrap_status": "bootstrap_pending",
            "bootstrap_attempt_count": 0,
        }

    def mark_ready(self, plan_id: str, command_id: str) -> dict[str, Any]:
        return self._mark_bootstrap(plan_id, command_id, status="ready")

    def mark_failed_recoverable(
        self,
        plan_id: str,
        command_id: str,
        *,
        error_code: str,
        retry_after_seconds: int,
    ) -> dict[str, Any]:
        return self._mark_bootstrap(
            plan_id,
            command_id,
            status="failed_recoverable",
            error_code=error_code,
            retry_after_seconds=retry_after_seconds,
        )

    def _mark_bootstrap(
        self,
        plan_id: str,
        command_id: str,
        *,
        status: str,
        error_code: str | None = None,
        retry_after_seconds: int = 0,
    ) -> dict[str, Any]:
        from psycopg2 import sql

        with self._provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        "UPDATE {commands} SET bootstrap_status=%s, "
                        "bootstrap_attempt_count=bootstrap_attempt_count+1, "
                        "last_bootstrap_attempt_at=NOW(), "
                        "next_retry_at=CASE WHEN %s > 0 THEN NOW() + (%s * INTERVAL '1 second') ELSE NULL END, "
                        "last_error_code=%s, last_error_retryable=%s, updated_at=NOW() "
                        "WHERE plan_id=%s AND command_id=%s"
                    ).format(commands=sql.Identifier(self._commands_table)),
                    (
                        status,
                        retry_after_seconds,
                        retry_after_seconds,
                        error_code,
                        status != "ready",
                        plan_id,
                        command_id,
                    ),
                )
                if cursor.rowcount != 1:
                    raise ValueError("launch command not found")
                command = self.select_command(cursor, plan_id, command_id)
            connection.commit()
        return command

    @staticmethod
    def _row(row) -> dict[str, Any] | None:
        if row is None:
            return None
        return {
            "plan_id": row[0],
            "command_id": row[1],
            "consumed_plan_version": row[2],
            "session_id": row[3],
            "bootstrap_status": row[4],
            "bootstrap_attempt_count": row[5],
            "last_bootstrap_attempt_at": row[6].isoformat() if row[6] else None,
            "next_retry_at": row[7].isoformat() if row[7] else None,
            "last_error_code": row[8],
            "last_error_retryable": row[9],
            "created_at": row[10].isoformat(),
            "updated_at": row[11].isoformat(),
        }
