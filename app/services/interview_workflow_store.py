from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Literal

from app.services.postgres_runtime_control import PostgresRuntimeControlStore
from app.services.runtime_domain_events import InterviewCommandReadyEvent


CommandType = Literal["answer", "skip", "finish"]
CommandStatus = Literal["pending", "applied", "conflict", "failed"]


class CommandPayloadConflict(ValueError):
    pass


@dataclass(frozen=True)
class InterviewCommandRecord:
    session_id: str
    command_id: str
    command_type: CommandType
    expected_version: int
    answer_text: str | None
    payload_sha256: str
    status: CommandStatus
    result_state_version: int | None
    error_code: str | None


class PostgresInterviewWorkflowStore:
    def __init__(self, *, dsn: str, table_prefix: str = "interview") -> None:
        self.dsn = dsn
        self.table_prefix = table_prefix
        self.sessions_table = f"{table_prefix}_sessions"
        self.commands_table = f"{table_prefix}_workflow_commands"
        self.control = PostgresRuntimeControlStore(
            dsn=dsn,
            table_prefix=table_prefix,
        )
        self._ensure_schema()

    def enqueue_command(
        self,
        *,
        session_id: str,
        command_id: str,
        command_type: CommandType,
        expected_version: int,
        answer_text: str | None = None,
    ) -> InterviewCommandRecord:
        if command_type == "answer" and not (answer_text or "").strip():
            raise ValueError("answer command requires answer_text")
        if command_type != "answer" and answer_text is not None:
            raise ValueError("only answer commands may carry answer_text")
        payload_sha256 = self._payload_sha256(
            command_type, expected_version, answer_text
        )
        with self.control.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    self._sql(
                        """
                        INSERT INTO {commands} (
                            session_id, command_id, command_type, expected_version,
                            answer_text, payload_sha256
                        )
                        VALUES (%s, %s, %s, %s, %s, %s)
                        ON CONFLICT (session_id, command_id) DO NOTHING
                        """
                    ),
                    (
                        session_id,
                        command_id,
                        command_type,
                        expected_version,
                        answer_text,
                        payload_sha256,
                    ),
                )
                if cursor.rowcount == 0:
                    existing = self._get_command(cursor, session_id, command_id)
                    if existing.payload_sha256 != payload_sha256:
                        raise CommandPayloadConflict(command_id)
                    return existing
                self.control.enqueue_event(
                    cursor,
                    InterviewCommandReadyEvent(
                        event_id=f"interview-command-{session_id}-{command_id}",
                        session_id=session_id,
                        causation_id=command_id,
                        command_id=command_id,
                    ),
                )
                return self._get_command(cursor, session_id, command_id)

    def get_command(
        self, session_id: str, command_id: str
    ) -> InterviewCommandRecord:
        with self.control.connection() as connection:
            with connection.cursor() as cursor:
                return self._get_command(cursor, session_id, command_id)

    def mark_command_applied(
        self, session_id: str, command_id: str, state_version: int
    ) -> None:
        self._update_status(
            session_id, command_id, "pending", "applied", state_version, None
        )

    def mark_command_conflict(
        self, session_id: str, command_id: str, state_version: int
    ) -> None:
        self._update_status(
            session_id,
            command_id,
            "pending",
            "conflict",
            state_version,
            "state_version_conflict",
        )

    def _update_status(
        self,
        session_id: str,
        command_id: str,
        expected_status: CommandStatus,
        status: CommandStatus,
        state_version: int,
        error_code: str | None,
    ) -> None:
        with self.control.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    self._sql(
                        """
                        UPDATE {commands}
                        SET status = %s, result_state_version = %s,
                            error_code = %s, completed_at = NOW(), updated_at = NOW()
                        WHERE session_id = %s AND command_id = %s AND status = %s
                        """
                    ),
                    (
                        status,
                        state_version,
                        error_code,
                        session_id,
                        command_id,
                        expected_status,
                    ),
                )
                if cursor.rowcount != 1:
                    raise ValueError("command status transition rejected")

    def _get_command(self, cursor, session_id: str, command_id: str):
        cursor.execute(
            self._sql(
                """
                SELECT session_id, command_id, command_type, expected_version,
                       answer_text, payload_sha256, status, result_state_version,
                       error_code
                FROM {commands}
                WHERE session_id = %s AND command_id = %s
                """
            ),
            (session_id, command_id),
        )
        row = cursor.fetchone()
        if row is None:
            raise ValueError("command not found")
        return InterviewCommandRecord(*row)

    def _ensure_schema(self) -> None:
        with self.control.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    self._sql(
                        """
                        CREATE TABLE IF NOT EXISTS {commands} (
                            session_id TEXT NOT NULL REFERENCES {sessions}(session_id)
                                ON DELETE CASCADE,
                            command_id TEXT NOT NULL,
                            command_type TEXT NOT NULL CHECK (
                                command_type IN ('answer', 'skip', 'finish')
                            ),
                            expected_version INTEGER NOT NULL CHECK (expected_version >= 1),
                            answer_text TEXT,
                            payload_sha256 TEXT NOT NULL,
                            status TEXT NOT NULL DEFAULT 'pending' CHECK (
                                status IN ('pending', 'applied', 'conflict', 'failed')
                            ),
                            result_state_version INTEGER,
                            error_code TEXT,
                            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            claimed_at TIMESTAMPTZ,
                            completed_at TIMESTAMPTZ,
                            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            PRIMARY KEY (session_id, command_id),
                            CHECK (
                                (command_type = 'answer' AND answer_text IS NOT NULL)
                                OR (command_type <> 'answer' AND answer_text IS NULL)
                            )
                        )
                        """
                    )
                )

    def _sql(self, statement: str):
        _, sql = self.control._import_psycopg2()
        return sql.SQL(statement).format(
            commands=sql.Identifier(self.commands_table),
            sessions=sql.Identifier(self.sessions_table),
        )

    @staticmethod
    def _payload_sha256(
        command_type: CommandType,
        expected_version: int,
        answer_text: str | None,
    ) -> str:
        payload = json.dumps(
            [command_type, expected_version, answer_text or ""],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
