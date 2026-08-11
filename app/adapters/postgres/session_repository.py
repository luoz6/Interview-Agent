from __future__ import annotations

import json
from typing import Any

from app.adapters.postgres.message_repository import PostgresMessageRepository
from app.adapters.postgres.row_mappers import SessionRowMapper
from app.adapters.postgres.session_repository_support import (
    iso_timestamp,
    postgres_sql,
)
from app.domain.interview.errors import SessionVersionConflict
from app.graphs.interview_state import InterviewState
from app.services.postgres_connections import ConnectionProvider
from app.services.runtime_domain_events import RoundClosedEvent


_postgres_sql = postgres_sql


class PostgresSessionRepository:
    def __init__(
        self,
        connection_provider: ConnectionProvider,
        *,
        sessions_table: str,
        message_repository: PostgresMessageRepository,
        runtime_outbox_repository: Any,
    ) -> None:
        self._connection_provider = connection_provider
        self.sessions_table = sessions_table
        self._messages = message_repository
        self._runtime_outbox = runtime_outbox_repository

    def get(self, session_id: str) -> InterviewState:
        sql = _postgres_sql()
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        """
                        SELECT session_id, plan_json, current_index, status,
                               phase, phase_status, review_status,
                               job_description, resume_text, job_tags,
                               decision_json, pending_output, skipped_question_ids,
                               started_at, finished_at, state_version,
                               checkpoint_version, last_checkpoint_at, last_command_id,
                               workflow_engine, graph_schema_version,
                               memory_policy_version, projection_sha256,
                               deletion_status, row_schema_version,
                               plan_binding_json
                        FROM {sessions}
                        WHERE session_id = %s
                        """
                    ).format(sessions=sql.Identifier(self.sessions_table)),
                    (session_id,),
                )
                row = cursor.fetchone()
                if row is None:
                    raise ValueError("session not found")
                session_row = self._session_row_from_db(row)
                cursor.execute(
                    sql.SQL(
                        """
                        SELECT sequence_no, role, content, question_id,
                               row_schema_version
                        FROM {messages}
                        WHERE session_id = %s
                        ORDER BY sequence_no
                        """
                    ).format(messages=sql.Identifier(self._messages.messages_table)),
                    (session_id,),
                )
                message_rows = [
                    {
                        "sequence_no": item[0],
                        "role": item[1],
                        "content": item[2],
                        "question_id": item[3],
                        "row_schema_version": item[4],
                    }
                    for item in cursor.fetchall()
                ]
        return SessionRowMapper.from_rows(session_row, message_rows)

    def insert_state(self, cursor: Any, state: InterviewState) -> None:
        sql = _postgres_sql()
        session_row = SessionRowMapper.to_row(state)
        cursor.execute(
            sql.SQL(
                """
                INSERT INTO {sessions} (
                    session_id, plan_json, current_index, status,
                    phase, phase_status, review_status,
                    job_description, resume_text, job_tags,
                    decision_json, pending_output, skipped_question_ids,
                    started_at, finished_at, state_version,
                    checkpoint_version, last_checkpoint_at, last_command_id,
                    workflow_engine, graph_schema_version,
                    memory_policy_version, projection_sha256,
                    deletion_status, row_schema_version, plan_binding_json
                )
                VALUES (
                    %s, %s::jsonb, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s::jsonb,
                    %s::jsonb, %s, %s::jsonb,
                    %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s::jsonb
                )
                """
            ).format(sessions=sql.Identifier(self.sessions_table)),
            (
                session_row["session_id"],
                json.dumps(session_row["plan_json"], ensure_ascii=False),
                session_row["current_index"],
                session_row["status"],
                session_row["phase"],
                session_row["phase_status"],
                session_row["review_status"],
                session_row["job_description"],
                session_row["resume_text"],
                json.dumps(session_row["job_tags"], ensure_ascii=False),
                json.dumps(session_row["decision_json"], ensure_ascii=False)
                if session_row["decision_json"] is not None
                else None,
                session_row["pending_output"],
                json.dumps(session_row["skipped_question_ids"], ensure_ascii=False),
                session_row["started_at"],
                session_row["finished_at"],
                session_row["state_version"],
                session_row["checkpoint_version"],
                session_row["last_checkpoint_at"],
                session_row["last_command_id"],
                session_row["workflow_engine"],
                session_row["graph_schema_version"],
                session_row["memory_policy_version"],
                session_row["projection_sha256"],
                session_row["deletion_status"],
                session_row["row_schema_version"],
                json.dumps(
                    session_row["plan_binding_json"],
                    ensure_ascii=False,
                ),
            ),
        )
        self._messages.insert_messages(cursor, state)

    def mark_deleting(self, cursor: Any, session_id: str) -> bool:
        sql = _postgres_sql()
        cursor.execute(
            sql.SQL(
                "UPDATE {sessions} SET deletion_status='deleting', "
                "updated_at=NOW() WHERE session_id=%s "
                "AND deletion_status='active'"
            ).format(sessions=sql.Identifier(self.sessions_table)),
            (session_id,),
        )
        changed = cursor.rowcount == 1
        if not changed:
            cursor.execute(
                sql.SQL(
                    "SELECT deletion_status FROM {sessions} WHERE session_id=%s"
                ).format(sessions=sql.Identifier(self.sessions_table)),
                (session_id,),
            )
            if cursor.fetchone() is None:
                raise ValueError("session not found")
        return changed

    def delete_session(self, cursor: Any, session_id: str) -> int:
        sql = _postgres_sql()
        cursor.execute(
            sql.SQL(
                "DELETE FROM {sessions} WHERE session_id=%s "
                "AND deletion_status='deleting'"
            ).format(sessions=sql.Identifier(self.sessions_table)),
            (session_id,),
        )
        return int(cursor.rowcount)

    def replace_state(
        self,
        cursor: Any,
        state: InterviewState,
        *,
        expected_previous_version: int | None = None,
        outbox_event: RoundClosedEvent | None = None,
    ) -> None:
        sql = _postgres_sql()
        session_row = SessionRowMapper.to_row(state)
        where_clause = sql.SQL("WHERE session_id = %s")
        update_params_suffix = [session_row["session_id"]]
        if expected_previous_version is not None:
            where_clause = sql.SQL("WHERE session_id = %s AND state_version = %s")
            update_params_suffix.append(expected_previous_version)

        self._messages.replace_messages(cursor, state)
        update_params = [
            json.dumps(session_row["plan_json"], ensure_ascii=False),
            session_row["current_index"],
            session_row["status"],
            session_row["phase"],
            session_row["phase_status"],
            session_row["review_status"],
            session_row["job_description"],
            session_row["resume_text"],
            json.dumps(session_row["job_tags"], ensure_ascii=False),
            json.dumps(session_row["decision_json"], ensure_ascii=False)
            if session_row["decision_json"] is not None
            else None,
            session_row["pending_output"],
            json.dumps(session_row["skipped_question_ids"], ensure_ascii=False),
            session_row["started_at"],
            session_row["state_version"],
            session_row["checkpoint_version"],
            session_row["last_checkpoint_at"],
            session_row["last_command_id"],
            session_row["workflow_engine"],
            session_row["graph_schema_version"],
            session_row["projection_sha256"],
            json.dumps(
                session_row["plan_binding_json"],
                ensure_ascii=False,
            ),
            session_row["row_schema_version"],
            session_row["status"],
            session_row["finished_at"],
            *update_params_suffix,
        ]
        cursor.execute(
            sql.SQL(
                """
                UPDATE {sessions}
                SET plan_json = %s::jsonb,
                    current_index = %s,
                    status = %s,
                    phase = %s,
                    phase_status = %s,
                    review_status = %s,
                    job_description = %s,
                    resume_text = %s,
                    job_tags = %s::jsonb,
                    decision_json = %s::jsonb,
                    pending_output = %s,
                    skipped_question_ids = %s::jsonb,
                    started_at = %s,
                    state_version = %s,
                    checkpoint_version = %s,
                    last_checkpoint_at = %s,
                    last_command_id = %s,
                    workflow_engine = %s,
                    graph_schema_version = %s,
                    projection_sha256 = %s,
                    plan_binding_json = %s::jsonb,
                    row_schema_version = %s,
                    updated_at = NOW(),
                    finished_at = CASE
                        WHEN %s = 'finished' THEN COALESCE(finished_at, %s)
                        ELSE finished_at
                    END
                {where_clause}
                """
            ).format(
                sessions=sql.Identifier(self.sessions_table),
                where_clause=where_clause,
            ),
            tuple(update_params),
        )
        if expected_previous_version is not None and cursor.rowcount == 0:
            cursor.execute(
                sql.SQL(
                    """
                    SELECT state_version
                    FROM {sessions}
                    WHERE session_id = %s
                    """
                ).format(sessions=sql.Identifier(self.sessions_table)),
                (session_row["session_id"],),
            )
            row = cursor.fetchone()
            if row is None:
                raise ValueError("session not found")
            raise SessionVersionConflict(
                expected_version=expected_previous_version,
                actual_version=row[0],
            )
        if outbox_event is not None:
            inserted = self._runtime_outbox.enqueue_event(cursor, outbox_event)
            if not inserted:
                raise RuntimeError("runtime event already exists for new transition")

    @staticmethod
    def _session_row_from_db(row: Any) -> dict[str, Any]:
        return {
            "session_id": row[0],
            "plan_json": row[1],
            "current_index": row[2],
            "status": row[3],
            "phase": row[4],
            "phase_status": row[5],
            "review_status": row[6],
            "job_description": row[7],
            "resume_text": row[8],
            "job_tags": row[9],
            "decision_json": row[10],
            "pending_output": row[11],
            "skipped_question_ids": row[12],
            "started_at": iso_timestamp(row[13]) or "",
            "finished_at": iso_timestamp(row[14]),
            "state_version": row[15],
            "checkpoint_version": row[16],
            "last_checkpoint_at": iso_timestamp(row[17]),
            "last_command_id": row[18],
            "workflow_engine": row[19],
            "graph_schema_version": row[20],
            "memory_policy_version": row[21],
            "projection_sha256": row[22],
            "deletion_status": row[23],
            "row_schema_version": row[24],
            "plan_binding_json": row[25],
        }


__all__ = ["PostgresSessionRepository"]
