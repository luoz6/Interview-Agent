from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from app.services.postgres_runtime_control import PostgresRuntimeControlStore
from app.services.runtime_domain_events import (
    InterviewCommandReadyEvent,
    InterviewRetryDueEvent,
)


CommandType = Literal["answer", "skip", "finish"]
CommandStatus = Literal["pending", "applied", "conflict", "failed"]


class CommandPayloadConflict(ValueError):
    pass


class ProjectionConflict(RuntimeError):
    pass


@dataclass(frozen=True)
class ProjectionResult:
    state_version: int
    projection_sha256: str


@dataclass(frozen=True)
class RetrySchedule:
    event_id: str
    available_at: datetime
    created_at: datetime


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

    def get_command_or_none(
        self, session_id: str, command_id: str
    ) -> InterviewCommandRecord | None:
        with self.control.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    self._sql(
                        """
                        SELECT session_id, command_id, command_type,
                               expected_version, answer_text, payload_sha256,
                               status, result_state_version, error_code
                        FROM {commands}
                        WHERE session_id = %s AND command_id = %s
                        """
                    ),
                    (session_id, command_id),
                )
                row = cursor.fetchone()
                return (
                    InterviewCommandRecord(*row)
                    if row is not None
                    else None
                )

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

    def project_state(self, state: dict[str, Any]) -> ProjectionResult:
        next_version = int(state["state_version"]) + 1
        payload = self._projection_payload(state, next_version)
        digest = hashlib.sha256(
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        with self.control.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    self._sql(
                        """
                        SELECT state_version, projection_sha256
                        FROM {sessions}
                        WHERE session_id = %s
                        FOR UPDATE
                        """
                    ),
                    (state["session_id"],),
                )
                current = cursor.fetchone()
                if current is None:
                    raise ValueError("session not found")
                current_version = int(current[0])
                current_digest = current[1]
                if current_version > next_version:
                    return ProjectionResult(current_version, current_digest)
                if current_version == next_version:
                    if current_digest != digest:
                        raise ProjectionConflict(state["session_id"])
                    self._verify_messages(cursor, state)
                    return ProjectionResult(next_version, digest)
                if current_version != int(state["state_version"]):
                    raise ProjectionConflict(state["session_id"])
                self._append_messages(cursor, state)
                cursor.execute(
                    self._sql(
                        """
                        UPDATE {sessions}
                        SET current_index = %s, status = %s,
                            skipped_question_ids = %s::jsonb,
                            state_version = %s, checkpoint_version = %s,
                            last_command_id = %s, projection_sha256 = %s,
                            updated_at = NOW(),
                            finished_at = CASE
                                WHEN %s = 'finished'
                                THEN COALESCE(finished_at, NOW())
                                ELSE finished_at
                            END
                        WHERE session_id = %s AND state_version = %s
                        """
                    ),
                    (
                        payload["current_index"],
                        payload["status"],
                        json.dumps(payload["skipped_question_ids"]),
                        next_version,
                        next_version,
                        payload["last_command_id"],
                        digest,
                        payload["status"],
                        state["session_id"],
                        state["state_version"],
                    ),
                )
                if cursor.rowcount != 1:
                    raise ProjectionConflict(state["session_id"])
                if (
                    state.get("command_outcome") == "completed"
                    and state.get("active_command_id")
                ):
                    cursor.execute(
                        self._sql(
                            """
                            UPDATE {commands}
                            SET status = 'applied', result_state_version = %s,
                                error_code = NULL, completed_at = NOW(),
                                updated_at = NOW()
                            WHERE session_id = %s AND command_id = %s
                              AND status = 'pending'
                            """
                        ),
                        (
                            next_version,
                            state["session_id"],
                            state["active_command_id"],
                        ),
                    )
                    if cursor.rowcount != 1:
                        raise ProjectionConflict(state["session_id"])
        return ProjectionResult(next_version, digest)

    def session_snapshot(self, session_id: str) -> dict[str, Any]:
        with self.control.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    self._sql(
                        """
                        SELECT state_version, status, projection_sha256
                        FROM {sessions} WHERE session_id = %s
                        """
                    ),
                    (session_id,),
                )
                row = cursor.fetchone()
        if row is None:
            raise ValueError("session not found")
        return {
            "state_version": row[0],
            "status": row[1],
            "projection_sha256": row[2],
        }

    def count_messages(self, session_id: str) -> int:
        with self.control.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    self._sql(
                        "SELECT COUNT(*) FROM {messages} WHERE session_id = %s"
                    ),
                    (session_id,),
                )
                return int(cursor.fetchone()[0])

    def enqueue_retry(
        self,
        *,
        session_id: str,
        generation_id: str,
        next_attempt_number: int,
        delay_seconds: float,
    ) -> RetrySchedule:
        event_id = f"{generation_id}:retry:{next_attempt_number}"
        event = InterviewRetryDueEvent(
            event_id=event_id,
            session_id=session_id,
            generation_id=generation_id,
            next_attempt_number=next_attempt_number,
        )
        with self.control.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    self._sql(
                        """
                        INSERT INTO {outbox} (
                            event_id, session_id, correlation_id, event_type,
                            schema_version, payload_json, status, available_at
                        )
                        VALUES (
                            %s, %s, %s, %s, %s, %s::jsonb, 'pending',
                            NOW() + (%s * INTERVAL '1 second')
                        )
                        ON CONFLICT (event_id) DO NOTHING
                        """
                    ),
                    (
                        event.event_id,
                        event.session_id,
                        event.correlation_id,
                        event.event_type,
                        event.schema_version,
                        event.model_dump_json(),
                        delay_seconds,
                    ),
                )
                cursor.execute(
                    self._sql(
                        """
                        SELECT event_id, available_at, created_at
                        FROM {outbox} WHERE event_id = %s
                        """
                    ),
                    (event_id,),
                )
                return RetrySchedule(*cursor.fetchone())

    def clear_applied_command_payloads(
        self, *, older_than: datetime
    ) -> int:
        with self.control.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    self._sql(
                        """
                        UPDATE {commands}
                        SET answer_text = NULL, updated_at = NOW()
                        WHERE status = 'applied' AND answer_text IS NOT NULL
                          AND completed_at < %s
                        """
                    ),
                    (older_than,),
                )
                return cursor.rowcount

    def clear_applied_command_payloads_older_than(
        self, *, hours: int
    ) -> int:
        if hours < 1:
            raise ValueError("retention hours must be positive")
        with self.control.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    self._sql(
                        """
                        UPDATE {commands}
                        SET answer_text = NULL, updated_at = NOW()
                        WHERE status = 'applied' AND answer_text IS NOT NULL
                          AND completed_at <
                              NOW() - (%s * INTERVAL '1 hour')
                        """
                    ),
                    (hours,),
                )
                return cursor.rowcount

    def delete_session_control_rows(self, session_id: str) -> int:
        with self.control.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    self._sql(
                        "DELETE FROM {commands} WHERE session_id = %s"
                    ),
                    (session_id,),
                )
                return cursor.rowcount

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
                _, sql = self.control._import_psycopg2()
                named_constraint = f"{self.commands_table}_answer_payload_check"
                cursor.execute(
                    sql.SQL(
                        "ALTER TABLE {commands} DROP CONSTRAINT IF EXISTS {constraint}"
                    ).format(
                        commands=sql.Identifier(self.commands_table),
                        constraint=sql.Identifier(named_constraint),
                    )
                )
                cursor.execute(
                    """
                    SELECT constraint_name
                    FROM information_schema.check_constraints
                    WHERE constraint_schema = current_schema()
                      AND constraint_name IN (
                          SELECT constraint_name
                          FROM information_schema.constraint_column_usage
                          WHERE table_name = %s AND column_name = 'answer_text'
                      )
                    """,
                    (self.commands_table,),
                )
                for (constraint_name,) in cursor.fetchall():
                    cursor.execute(
                        sql.SQL(
                            "ALTER TABLE {commands} DROP CONSTRAINT {constraint}"
                        ).format(
                            commands=sql.Identifier(self.commands_table),
                            constraint=sql.Identifier(constraint_name),
                        )
                    )
                constraint_name = named_constraint
                cursor.execute(
                    sql.SQL(
                        """
                        ALTER TABLE {commands}
                        ADD CONSTRAINT {constraint}
                        CHECK (
                            (
                                command_type = 'answer'
                                AND (
                                    answer_text IS NOT NULL
                                    OR status = 'applied'
                                )
                            )
                            OR (
                                command_type <> 'answer'
                                AND answer_text IS NULL
                            )
                        )
                        """
                    ).format(
                        commands=sql.Identifier(self.commands_table),
                        constraint=sql.Identifier(constraint_name),
                    )
                )
                cursor.execute(
                    sql.SQL(
                        """
                        CREATE INDEX IF NOT EXISTS {index}
                        ON {commands} (status, updated_at)
                        """
                    ).format(
                        index=sql.Identifier(
                            f"{self.commands_table}_status_updated_idx"
                        ),
                        commands=sql.Identifier(self.commands_table),
                    )
                )

    def _sql(self, statement: str):
        _, sql = self.control._import_psycopg2()
        return sql.SQL(statement).format(
            commands=sql.Identifier(self.commands_table),
            sessions=sql.Identifier(self.sessions_table),
            messages=sql.Identifier(f"{self.table_prefix}_messages"),
            outbox=sql.Identifier(self.control.outbox_table),
        )

    def _append_messages(self, cursor, state: dict[str, Any]) -> None:
        self._verify_messages(cursor, state, append_missing=True)

    def _verify_messages(
        self,
        cursor,
        state: dict[str, Any],
        *,
        append_missing: bool = False,
    ) -> None:
        cursor.execute(
            self._sql(
                """
                SELECT sequence_no, role, content, question_id
                FROM {messages}
                WHERE session_id = %s
                ORDER BY sequence_no
                """
            ),
            (state["session_id"],),
        )
        existing = cursor.fetchall()
        messages = state["messages"]
        for index, row in enumerate(existing):
            if index >= len(messages):
                raise ProjectionConflict(state["session_id"])
            message = messages[index]
            if row != (
                index + 1,
                message["role"],
                message["content"],
                message["question_id"],
            ):
                raise ProjectionConflict(state["session_id"])
        if not append_missing and len(existing) != len(messages):
            raise ProjectionConflict(state["session_id"])
        for index, message in enumerate(
            messages[len(existing) :], start=len(existing) + 1
        ):
            cursor.execute(
                self._sql(
                    """
                    INSERT INTO {messages} (
                        session_id, sequence_no, role, content, question_id
                    )
                    VALUES (%s, %s, %s, %s, %s)
                    """
                ),
                (
                    state["session_id"],
                    index,
                    message["role"],
                    message["content"],
                    message["question_id"],
                ),
            )

    @staticmethod
    def _projection_payload(
        state: dict[str, Any], state_version: int
    ) -> dict[str, Any]:
        return {
            "session_id": state["session_id"],
            "current_index": state["current_index"],
            "messages": state["messages"],
            "skipped_question_ids": state["skipped_question_ids"],
            "status": state["interview_status"],
            "state_version": state_version,
            "last_command_id": state.get("active_command_id")
            or state.get("last_command_id"),
        }

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
