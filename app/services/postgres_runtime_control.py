from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Any

from app.services.question_evaluations import QuestionEvaluationRecord
from app.services.agent_runtime import AgentRunRecord
from app.services.runtime_domain_events import RoundClosedEvent
from app.services.session_serialization import (
    question_evaluation_record_to_row,
)


class PostgresRuntimeControlStore:
    _OUTBOX_COLUMNS = """
        event_id, session_id, correlation_id, event_type, schema_version,
        payload_json, status, attempt_count, max_attempts, available_at,
        lease_owner, lease_expires_at, last_error_code, replay_count,
        created_at, updated_at, published_at, dead_lettered_at
    """
    _RECEIPT_COLUMNS = """
        event_id, consumer_name, session_id, correlation_id, event_type,
        schema_version, status, attempt_count, max_attempts, available_at,
        lease_owner, lease_expires_at, last_error_code, replay_count,
        started_at, completed_at, dead_lettered_at, created_at, updated_at
    """

    def __init__(
        self,
        *,
        dsn: str,
        table_prefix: str = "interview",
    ) -> None:
        self.dsn = dsn
        self.table_prefix = table_prefix
        self.sessions_table = f"{table_prefix}_sessions"
        self.question_evaluations_table = (
            f"{table_prefix}_question_evaluations"
        )
        self.outbox_table = f"{table_prefix}_runtime_outbox"
        self.receipts_table = f"{table_prefix}_runtime_event_receipts"
        self.agent_runs_table = f"{table_prefix}_agent_runs"
        self._ensure_schema()

    @contextmanager
    def connection(self) -> Iterator[Any]:
        psycopg2, _ = self._import_psycopg2()
        with psycopg2.connect(self.dsn) as connection:
            yield connection

    def enqueue_event(self, cursor, event: RoundClosedEvent) -> bool:
        _, sql = self._import_psycopg2()
        cursor.execute(
            sql.SQL(
                """
                INSERT INTO {outbox} (
                    event_id, session_id, correlation_id, event_type,
                    schema_version, payload_json, status
                )
                VALUES (%s, %s, %s, %s, %s, %s::jsonb, 'pending')
                ON CONFLICT (event_id) DO NOTHING
                """
            ).format(outbox=sql.Identifier(self.outbox_table)),
            (
                event.event_id,
                event.session_id,
                event.correlation_id,
                event.event_type,
                event.schema_version,
                event.model_dump_json(),
            ),
        )
        return cursor.rowcount == 1

    def count_outbox(self, event_id: str | None = None) -> int:
        _, sql = self._import_psycopg2()
        statement = sql.SQL("SELECT COUNT(*) FROM {outbox}").format(
            outbox=sql.Identifier(self.outbox_table)
        )
        params: tuple[Any, ...] = ()
        if event_id is not None:
            statement += sql.SQL(" WHERE event_id = %s")
            params = (event_id,)
        with self.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(statement, params)
                row = cursor.fetchone()
        return int(row[0])

    def list_outbox(
        self,
        *,
        session_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        _, sql = self._import_psycopg2()
        clauses = []
        params: list[Any] = []
        if session_id is not None:
            clauses.append(sql.SQL("session_id = %s"))
            params.append(session_id)
        if status is not None:
            clauses.append(sql.SQL("status = %s"))
            params.append(status)
        where = (
            sql.SQL(" WHERE ") + sql.SQL(" AND ").join(clauses)
            if clauses
            else sql.SQL("")
        )
        params.append(limit)
        with self.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        f"""
                        SELECT {self._OUTBOX_COLUMNS}
                        FROM {{outbox}}
                        {{where}}
                        ORDER BY created_at, event_id
                        LIMIT %s
                        """
                    ).format(
                        outbox=sql.Identifier(self.outbox_table),
                        where=where,
                    ),
                    tuple(params),
                )
                rows = cursor.fetchall()
        return [self._outbox_row_to_dict(row) for row in rows]

    def list_runtime_events(
        self,
        *,
        session_id: str,
        status: str | None = None,
        event_type: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        _, sql = self._import_psycopg2()
        clauses = [sql.SQL("session_id = %s")]
        params: list[Any] = [session_id]
        if status is not None:
            clauses.append(sql.SQL("status = %s"))
            params.append(status)
        if event_type is not None:
            clauses.append(sql.SQL("event_type = %s"))
            params.append(event_type)
        params.append(limit)
        with self.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        """
                        SELECT event_id, correlation_id, event_type,
                               schema_version, status, attempt_count,
                               max_attempts, replay_count, last_error_code,
                               created_at, updated_at, published_at,
                               dead_lettered_at
                        FROM {outbox}
                        WHERE {where}
                        ORDER BY created_at DESC, event_id
                        LIMIT %s
                        """
                    ).format(
                        outbox=sql.Identifier(self.outbox_table),
                        where=sql.SQL(" AND ").join(clauses),
                    ),
                    tuple(params),
                )
                rows = cursor.fetchall()
        return [
            {
                "event_id": row[0],
                "correlation_id": row[1],
                "event_type": row[2],
                "schema_version": row[3],
                "status": row[4],
                "attempt_count": row[5],
                "max_attempts": row[6],
                "replay_count": row[7],
                "last_error_code": row[8],
                "created_at": row[9],
                "updated_at": row[10],
                "published_at": row[11],
                "dead_lettered_at": row[12],
            }
            for row in rows
        ]

    def list_recovery_events(
        self,
        *,
        status: str,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        _, sql = self._import_psycopg2()
        with self.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        """
                        SELECT event_id, session_id, correlation_id,
                               event_type, status, attempt_count,
                               max_attempts, replay_count, last_error_code,
                               available_at, created_at, updated_at,
                               published_at, dead_lettered_at
                        FROM {outbox}
                        WHERE status = %s
                        ORDER BY updated_at, event_id
                        LIMIT %s
                        """
                    ).format(outbox=sql.Identifier(self.outbox_table)),
                    (status, limit),
                )
                rows = cursor.fetchall()
        return [
            {
                "event_id": row[0],
                "session_id": row[1],
                "correlation_id": row[2],
                "event_type": row[3],
                "status": row[4],
                "attempt_count": row[5],
                "max_attempts": row[6],
                "replay_count": row[7],
                "last_error_code": row[8],
                "available_at": row[9],
                "created_at": row[10],
                "updated_at": row[11],
                "published_at": row[12],
                "dead_lettered_at": row[13],
            }
            for row in rows
        ]

    def list_foreign_keys(self) -> dict[str, tuple[str, str]]:
        table_names = [
            self.outbox_table,
            self.receipts_table,
            self.agent_runs_table,
        ]
        with self.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT tc.table_name, kcu.column_name, rc.delete_rule
                    FROM information_schema.table_constraints AS tc
                    JOIN information_schema.key_column_usage AS kcu
                      ON tc.constraint_name = kcu.constraint_name
                     AND tc.constraint_schema = kcu.constraint_schema
                    JOIN information_schema.referential_constraints AS rc
                      ON tc.constraint_name = rc.constraint_name
                     AND tc.constraint_schema = rc.constraint_schema
                    JOIN information_schema.constraint_column_usage AS ccu
                      ON rc.unique_constraint_name = ccu.constraint_name
                     AND rc.unique_constraint_schema = ccu.constraint_schema
                    WHERE tc.constraint_type = 'FOREIGN KEY'
                      AND tc.table_name = ANY(%s)
                      AND ccu.table_name = %s
                    ORDER BY tc.table_name
                    """,
                    (table_names, self.sessions_table),
                )
                rows = cursor.fetchall()
        return {row[0]: (row[1], row[2]) for row in rows}

    def list_control_tables(self) -> list[str]:
        names = [
            self.outbox_table,
            self.receipts_table,
            self.agent_runs_table,
        ]
        with self.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT table_name
                    FROM information_schema.tables
                    WHERE table_schema = 'public'
                      AND table_name = ANY(%s)
                    ORDER BY table_name
                    """,
                    (names,),
                )
                return [row[0] for row in cursor.fetchall()]

    def list_control_indexes(self) -> list[str]:
        names = [
            self.outbox_table,
            self.receipts_table,
            self.agent_runs_table,
        ]
        with self.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT indexname
                    FROM pg_indexes
                    WHERE schemaname = 'public'
                      AND tablename = ANY(%s)
                    ORDER BY indexname
                    """,
                    (names,),
                )
                return [row[0] for row in cursor.fetchall()]

    def delete_agent_runs_by_correlation(
        self,
        correlation_id: str,
    ) -> int:
        _, sql = self._import_psycopg2()
        with self.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        """
                        DELETE FROM {agent_runs}
                        WHERE correlation_id = %s
                        """
                    ).format(
                        agent_runs=sql.Identifier(self.agent_runs_table)
                    ),
                    (correlation_id,),
                )
                count = cursor.rowcount
        return count

    def claim_batch(
        self,
        *,
        worker_id: str,
        limit: int,
        lease_seconds: int,
    ) -> list[dict[str, Any]]:
        if not worker_id:
            raise ValueError("worker_id is required")
        if limit < 1:
            raise ValueError("limit must be positive")
        if lease_seconds < 1:
            raise ValueError("lease_seconds must be positive")
        _, sql = self._import_psycopg2()
        with self.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        """
                        SELECT event_id
                        FROM {outbox}
                        WHERE (
                            status = 'pending'
                            OR (
                                status = 'retrying'
                                AND available_at <= NOW()
                            )
                            OR (
                                status = 'running'
                                AND lease_expires_at <= NOW()
                            )
                        )
                        ORDER BY available_at, created_at, event_id
                        FOR UPDATE SKIP LOCKED
                        LIMIT %s
                        """
                    ).format(outbox=sql.Identifier(self.outbox_table)),
                    (limit,),
                )
                event_ids = [row[0] for row in cursor.fetchall()]
                if not event_ids:
                    return []
                cursor.execute(
                    sql.SQL(
                        f"""
                        UPDATE {{outbox}}
                        SET status = 'running',
                            attempt_count = attempt_count + 1,
                            lease_owner = %s,
                            lease_expires_at =
                                NOW() + (%s * INTERVAL '1 second'),
                            last_error_code = NULL,
                            updated_at = NOW()
                        WHERE event_id = ANY(%s)
                        RETURNING {self._OUTBOX_COLUMNS}
                        """
                    ).format(outbox=sql.Identifier(self.outbox_table)),
                    (worker_id, lease_seconds, event_ids),
                )
                rows = cursor.fetchall()
        by_id = {
            row[0]: self._outbox_row_to_dict(row)
            for row in rows
        }
        return [by_id[event_id] for event_id in event_ids]

    def mark_published(
        self,
        event_id: str,
        worker_id: str,
    ) -> dict[str, Any] | None:
        _, sql = self._import_psycopg2()
        with self.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        f"""
                        UPDATE {{outbox}}
                        SET status = 'published',
                            lease_owner = NULL,
                            lease_expires_at = NULL,
                            last_error_code = NULL,
                            published_at = NOW(),
                            updated_at = NOW()
                        WHERE event_id = %s
                          AND status = 'running'
                          AND lease_owner = %s
                        RETURNING {self._OUTBOX_COLUMNS}
                        """
                    ).format(outbox=sql.Identifier(self.outbox_table)),
                    (event_id, worker_id),
                )
                row = cursor.fetchone()
        return self._outbox_row_to_dict(row)

    def mark_retrying(
        self,
        event_id: str,
        worker_id: str,
        *,
        error_code: str,
        available_at: datetime,
    ) -> dict[str, Any] | None:
        _, sql = self._import_psycopg2()
        with self.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        f"""
                        UPDATE {{outbox}}
                        SET status = 'retrying',
                            available_at = %s,
                            lease_owner = NULL,
                            lease_expires_at = NULL,
                            last_error_code = %s,
                            updated_at = NOW()
                        WHERE event_id = %s
                          AND status = 'running'
                          AND lease_owner = %s
                        RETURNING {self._OUTBOX_COLUMNS}
                        """
                    ).format(outbox=sql.Identifier(self.outbox_table)),
                    (available_at, error_code, event_id, worker_id),
                )
                row = cursor.fetchone()
        return self._outbox_row_to_dict(row)

    def mark_dead_letter(
        self,
        event_id: str,
        worker_id: str,
        *,
        error_code: str,
    ) -> dict[str, Any] | None:
        _, sql = self._import_psycopg2()
        with self.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        f"""
                        UPDATE {{outbox}}
                        SET status = 'dead_letter',
                            lease_owner = NULL,
                            lease_expires_at = NULL,
                            last_error_code = %s,
                            dead_lettered_at = NOW(),
                            updated_at = NOW()
                        WHERE event_id = %s
                          AND status = 'running'
                          AND lease_owner = %s
                        RETURNING {self._OUTBOX_COLUMNS}
                        """
                    ).format(outbox=sql.Identifier(self.outbox_table)),
                    (error_code, event_id, worker_id),
                )
                row = cursor.fetchone()
        return self._outbox_row_to_dict(row)

    def release_expired_leases(self) -> int:
        _, sql = self._import_psycopg2()
        with self.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        """
                        UPDATE {outbox}
                        SET status = 'retrying',
                            available_at = NOW(),
                            lease_owner = NULL,
                            lease_expires_at = NULL,
                            last_error_code = 'dispatcher_lease_expired',
                            updated_at = NOW()
                        WHERE status = 'running'
                          AND lease_expires_at <= NOW()
                        """
                    ).format(outbox=sql.Identifier(self.outbox_table))
                )
                count = cursor.rowcount
        return count

    def replay_dead_letter(
        self,
        event_id: str,
    ) -> dict[str, Any]:
        _, sql = self._import_psycopg2()
        with self.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        f"""
                        SELECT {self._OUTBOX_COLUMNS}
                        FROM {{outbox}}
                        WHERE event_id = %s
                        FOR UPDATE
                        """
                    ).format(outbox=sql.Identifier(self.outbox_table)),
                    (event_id,),
                )
                existing = self._outbox_row_to_dict(cursor.fetchone())
                if (
                    existing is None
                    or existing["status"] != "dead_letter"
                ):
                    raise ValueError("event is not dead-lettered")
                cursor.execute(
                    sql.SQL(
                        f"""
                        UPDATE {{outbox}}
                        SET status = 'pending',
                            attempt_count = 0,
                            available_at = NOW(),
                            lease_owner = NULL,
                            lease_expires_at = NULL,
                            last_error_code = NULL,
                            replay_count = replay_count + 1,
                            published_at = NULL,
                            dead_lettered_at = NULL,
                            updated_at = NOW()
                        WHERE event_id = %s
                        RETURNING {self._OUTBOX_COLUMNS}
                        """
                    ).format(outbox=sql.Identifier(self.outbox_table)),
                    (event_id,),
                )
                replayed = self._outbox_row_to_dict(cursor.fetchone())
                cursor.execute(
                    sql.SQL(
                        """
                        UPDATE {receipts}
                        SET status = 'retrying',
                            attempt_count = 0,
                            available_at = NOW(),
                            lease_owner = NULL,
                            lease_expires_at = NULL,
                            last_error_code = NULL,
                            replay_count = replay_count + 1,
                            dead_lettered_at = NULL,
                            updated_at = NOW()
                        WHERE event_id = %s
                          AND status = 'dead_letter'
                        """
                    ).format(
                        receipts=sql.Identifier(self.receipts_table)
                    ),
                    (event_id,),
                )
        return replayed

    def record_agent_run(self, record: AgentRunRecord) -> bool:
        _, sql = self._import_psycopg2()
        with self.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        """
                        INSERT INTO {agent_runs} (
                            run_id, schema_version, correlation_id,
                            causation_id, agent, operation, phase,
                            session_id, question_id, state_version,
                            command_id, evidence_ids, attempt_number,
                            status, started_at, finished_at, latency_ms,
                            fallback_reason, error_code, output_type,
                            safe_metadata
                        )
                        VALUES (
                            %s, %s, %s, %s, %s, %s, %s,
                            %s, %s, %s, %s, %s::jsonb, %s,
                            %s, %s, %s, %s, %s, %s, %s, %s::jsonb
                        )
                        ON CONFLICT (run_id) DO NOTHING
                        """
                    ).format(
                        agent_runs=sql.Identifier(self.agent_runs_table)
                    ),
                    (
                        record.run_id,
                        record.schema_version,
                        record.correlation_id,
                        record.causation_id,
                        record.agent,
                        record.operation,
                        record.phase,
                        record.session_id,
                        record.question_id,
                        record.state_version,
                        record.command_id,
                        json.dumps(record.evidence_ids),
                        record.attempt_number,
                        record.status,
                        record.started_at,
                        record.finished_at,
                        record.latency_ms,
                        record.fallback_reason,
                        record.error_code,
                        record.output_type,
                        json.dumps(
                            record.safe_metadata,
                            ensure_ascii=False,
                        ),
                    ),
                )
                inserted = cursor.rowcount == 1
        return inserted

    def count_agent_runs(self, run_id: str | None = None) -> int:
        _, sql = self._import_psycopg2()
        statement = sql.SQL(
            "SELECT COUNT(*) FROM {agent_runs}"
        ).format(agent_runs=sql.Identifier(self.agent_runs_table))
        params: tuple[Any, ...] = ()
        if run_id is not None:
            statement += sql.SQL(" WHERE run_id = %s")
            params = (run_id,)
        with self.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(statement, params)
                row = cursor.fetchone()
        return int(row[0])

    def list_agent_runs(
        self,
        *,
        session_id: str | None = None,
        correlation_id: str | None = None,
        agent: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        _, sql = self._import_psycopg2()
        clauses = []
        params: list[Any] = []
        identity_clauses = []
        if session_id is not None:
            identity_clauses.append(sql.SQL("session_id = %s"))
            params.append(session_id)
        if correlation_id is not None:
            identity_clauses.append(sql.SQL("correlation_id = %s"))
            params.append(correlation_id)
        if identity_clauses:
            clauses.append(
                sql.SQL("(")
                + sql.SQL(" OR ").join(identity_clauses)
                + sql.SQL(")")
            )
        if agent is not None:
            clauses.append(sql.SQL("agent = %s"))
            params.append(agent)
        if status is not None:
            clauses.append(sql.SQL("status = %s"))
            params.append(status)
        where = (
            sql.SQL(" WHERE ") + sql.SQL(" AND ").join(clauses)
            if clauses
            else sql.SQL("")
        )
        params.append(limit)
        with self.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        """
                        SELECT run_id, correlation_id, causation_id,
                               agent, operation, phase, session_id,
                               question_id, state_version, command_id,
                               evidence_ids, attempt_number, status,
                               started_at, finished_at, latency_ms,
                               fallback_reason, error_code, output_type
                        FROM {agent_runs}
                        {where}
                        ORDER BY started_at DESC, run_id
                        LIMIT %s
                        """
                    ).format(
                        agent_runs=sql.Identifier(
                            self.agent_runs_table
                        ),
                        where=where,
                    ),
                    tuple(params),
                )
                rows = cursor.fetchall()
        return [
            {
                "run_id": row[0],
                "correlation_id": row[1],
                "causation_id": row[2],
                "agent": row[3],
                "operation": row[4],
                "phase": row[5],
                "session_id": row[6],
                "question_id": row[7],
                "state_version": row[8],
                "command_id": row[9],
                "evidence_ids": row[10],
                "attempt_number": row[11],
                "status": row[12],
                "started_at": row[13],
                "finished_at": row[14],
                "latency_ms": row[15],
                "fallback_reason": row[16],
                "error_code": row[17],
                "output_type": row[18],
            }
            for row in rows
        ]

    def claim_receipt(
        self,
        event: RoundClosedEvent,
        *,
        consumer_name: str,
        worker_id: str,
        lease_seconds: int,
        max_attempts: int = 5,
    ) -> dict[str, Any]:
        if not consumer_name or not worker_id:
            raise ValueError("consumer_name and worker_id are required")
        if lease_seconds < 1 or max_attempts < 1:
            raise ValueError("receipt lease and attempts must be positive")
        _, sql = self._import_psycopg2()
        with self.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        """
                        INSERT INTO {receipts} (
                            event_id, consumer_name, session_id,
                            correlation_id, event_type, schema_version,
                            status, max_attempts
                        )
                        VALUES (
                            %s, %s, %s, %s, %s, %s, 'retrying', %s
                        )
                        ON CONFLICT (event_id, consumer_name) DO NOTHING
                        """
                    ).format(receipts=sql.Identifier(self.receipts_table)),
                    (
                        event.event_id,
                        consumer_name,
                        event.session_id,
                        event.correlation_id,
                        event.event_type,
                        event.schema_version,
                        max_attempts,
                    ),
                )
                cursor.execute(
                    sql.SQL(
                        f"""
                        SELECT {self._RECEIPT_COLUMNS},
                               GREATEST(
                                   0,
                                   CEIL(EXTRACT(
                                       EPOCH FROM lease_expires_at - NOW()
                                   ))
                               )::INTEGER AS lease_remaining_seconds,
                               GREATEST(
                                   0,
                                   CEIL(EXTRACT(
                                       EPOCH FROM available_at - NOW()
                                   ))
                               )::INTEGER AS retry_remaining_seconds
                        FROM {{receipts}}
                        WHERE event_id = %s AND consumer_name = %s
                        FOR UPDATE
                        """
                    ).format(
                        receipts=sql.Identifier(self.receipts_table)
                    ),
                    (event.event_id, consumer_name),
                )
                row = cursor.fetchone()
                if row is None:
                    raise ValueError("runtime outbox event not found")
                receipt = self._receipt_row_to_dict(row)
                if receipt["status"] == "completed":
                    return {**receipt, "claim_status": "completed"}
                if receipt["status"] == "dead_letter":
                    return {**receipt, "claim_status": "dead_letter"}
                if (
                    receipt["status"] == "running"
                    and row[19] is not None
                    and row[19] > 0
                ):
                    return {
                        **receipt,
                        "claim_status": "active",
                        "countdown_seconds": row[19],
                    }
                if (
                    receipt["status"] == "retrying"
                    and row[20] > 0
                ):
                    return {
                        **receipt,
                        "claim_status": "retry_wait",
                        "countdown_seconds": row[20],
                    }
                cursor.execute(
                    sql.SQL(
                        f"""
                        UPDATE {{receipts}}
                        SET status = 'running',
                            attempt_count = attempt_count + 1,
                            lease_owner = %s,
                            lease_expires_at =
                                NOW() + (%s * INTERVAL '1 second'),
                            started_at = COALESCE(started_at, NOW()),
                            last_error_code = NULL,
                            updated_at = NOW()
                        WHERE event_id = %s AND consumer_name = %s
                        RETURNING {self._RECEIPT_COLUMNS}
                        """
                    ).format(
                        receipts=sql.Identifier(self.receipts_table)
                    ),
                    (
                        worker_id,
                        lease_seconds,
                        event.event_id,
                        consumer_name,
                    ),
                )
                claimed = self._receipt_row_to_dict(cursor.fetchone())
        return {**claimed, "claim_status": "claimed"}

    def get_receipt(
        self,
        event_id: str,
        consumer_name: str,
    ) -> dict[str, Any] | None:
        _, sql = self._import_psycopg2()
        with self.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        f"""
                        SELECT {self._RECEIPT_COLUMNS}
                        FROM {{receipts}}
                        WHERE event_id = %s AND consumer_name = %s
                        """
                    ).format(
                        receipts=sql.Identifier(self.receipts_table)
                    ),
                    (event_id, consumer_name),
                )
                row = cursor.fetchone()
        return self._receipt_row_to_dict(row)

    def mark_receipt_retrying(
        self,
        event_id: str,
        consumer_name: str,
        worker_id: str,
        *,
        error_code: str,
        available_at: datetime,
    ) -> dict[str, Any] | None:
        _, sql = self._import_psycopg2()
        with self.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        f"""
                        UPDATE {{receipts}}
                        SET status = 'retrying',
                            available_at = %s,
                            lease_owner = NULL,
                            lease_expires_at = NULL,
                            last_error_code = %s,
                            updated_at = NOW()
                        WHERE event_id = %s
                          AND consumer_name = %s
                          AND status = 'running'
                          AND lease_owner = %s
                        RETURNING {self._RECEIPT_COLUMNS}
                        """
                    ).format(
                        receipts=sql.Identifier(self.receipts_table)
                    ),
                    (
                        available_at,
                        error_code,
                        event_id,
                        consumer_name,
                        worker_id,
                    ),
                )
                row = cursor.fetchone()
        return self._receipt_row_to_dict(row)

    def complete_round_review(
        self,
        event_id: str,
        consumer_name: str,
        worker_id: str,
        record: QuestionEvaluationRecord,
    ) -> dict[str, Any]:
        return self._finish_round_review(
            event_id,
            consumer_name,
            worker_id,
            record,
            status="completed",
            error_code=None,
        )

    def fail_round_review(
        self,
        event_id: str,
        consumer_name: str,
        worker_id: str,
        record: QuestionEvaluationRecord,
        *,
        error_code: str,
    ) -> dict[str, Any]:
        return self._finish_round_review(
            event_id,
            consumer_name,
            worker_id,
            record,
            status="dead_letter",
            error_code=error_code,
        )

    def _finish_round_review(
        self,
        event_id: str,
        consumer_name: str,
        worker_id: str,
        record: QuestionEvaluationRecord,
        *,
        status: str,
        error_code: str | None,
    ) -> dict[str, Any]:
        _, sql = self._import_psycopg2()
        timestamp_column = (
            "completed_at"
            if status == "completed"
            else "dead_lettered_at"
        )
        with self.connection() as connection:
            with connection.cursor() as cursor:
                self._upsert_question_evaluation(cursor, sql, record)
                cursor.execute(
                    sql.SQL(
                        f"""
                        UPDATE {{receipts}}
                        SET status = %s,
                            lease_owner = NULL,
                            lease_expires_at = NULL,
                            last_error_code = %s,
                            {timestamp_column} = NOW(),
                            updated_at = NOW()
                        WHERE event_id = %s
                          AND consumer_name = %s
                          AND status = 'running'
                          AND lease_owner = %s
                        RETURNING {self._RECEIPT_COLUMNS}
                        """
                    ).format(
                        receipts=sql.Identifier(self.receipts_table)
                    ),
                    (
                        status,
                        error_code,
                        event_id,
                        consumer_name,
                        worker_id,
                    ),
                )
                row = cursor.fetchone()
                if row is None:
                    raise RuntimeError("runtime receipt lease was lost")
        return self._receipt_row_to_dict(row)

    def _upsert_question_evaluation(
        self,
        cursor,
        sql,
        record: QuestionEvaluationRecord,
    ) -> None:
        row = question_evaluation_record_to_row(record)
        cursor.execute(
            sql.SQL(
                """
                INSERT INTO {evaluations} (
                    session_id, question_id, answer_state, status,
                    feedback_json, error, created_at
                )
                VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s)
                ON CONFLICT (session_id, question_id) DO UPDATE
                SET answer_state = EXCLUDED.answer_state,
                    status = EXCLUDED.status,
                    feedback_json = EXCLUDED.feedback_json,
                    error = EXCLUDED.error,
                    updated_at = NOW()
                """
            ).format(
                evaluations=sql.Identifier(
                    self.question_evaluations_table
                )
            ),
            (
                row["session_id"],
                row["question_id"],
                row["answer_state"],
                row["status"],
                json.dumps(row["feedback_json"], ensure_ascii=False)
                if row["feedback_json"] is not None
                else None,
                row["error"],
                row["created_at"],
            ),
        )

    def _ensure_schema(self) -> None:
        psycopg2, sql = self._import_psycopg2()
        with psycopg2.connect(self.dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        """
                        CREATE TABLE IF NOT EXISTS {outbox} (
                            event_id TEXT PRIMARY KEY,
                            session_id TEXT NOT NULL
                                REFERENCES {sessions}(session_id)
                                ON DELETE CASCADE,
                            correlation_id TEXT NOT NULL,
                            event_type TEXT NOT NULL,
                            schema_version TEXT NOT NULL,
                            payload_json JSONB NOT NULL,
                            status TEXT NOT NULL DEFAULT 'pending'
                                CHECK (status IN (
                                    'pending', 'running', 'retrying',
                                    'published', 'dead_letter'
                                )),
                            attempt_count INTEGER NOT NULL DEFAULT 0
                                CHECK (attempt_count >= 0),
                            max_attempts INTEGER NOT NULL DEFAULT 5
                                CHECK (max_attempts > 0),
                            available_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            lease_owner TEXT,
                            lease_expires_at TIMESTAMPTZ,
                            last_error_code TEXT,
                            replay_count INTEGER NOT NULL DEFAULT 0
                                CHECK (replay_count >= 0),
                            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            published_at TIMESTAMPTZ,
                            dead_lettered_at TIMESTAMPTZ
                        )
                        """
                    ).format(
                        outbox=sql.Identifier(self.outbox_table),
                        sessions=sql.Identifier(self.sessions_table),
                    )
                )
                cursor.execute(
                    sql.SQL(
                        """
                        CREATE TABLE IF NOT EXISTS {receipts} (
                            event_id TEXT NOT NULL
                                REFERENCES {outbox}(event_id)
                                ON DELETE CASCADE,
                            consumer_name TEXT NOT NULL,
                            session_id TEXT NOT NULL
                                REFERENCES {sessions}(session_id)
                                ON DELETE CASCADE,
                            correlation_id TEXT NOT NULL,
                            event_type TEXT NOT NULL,
                            schema_version TEXT NOT NULL,
                            status TEXT NOT NULL
                                CHECK (status IN (
                                    'running', 'retrying',
                                    'completed', 'dead_letter'
                                )),
                            attempt_count INTEGER NOT NULL DEFAULT 0
                                CHECK (attempt_count >= 0),
                            max_attempts INTEGER NOT NULL DEFAULT 5
                                CHECK (max_attempts > 0),
                            available_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            lease_owner TEXT,
                            lease_expires_at TIMESTAMPTZ,
                            last_error_code TEXT,
                            replay_count INTEGER NOT NULL DEFAULT 0
                                CHECK (replay_count >= 0),
                            started_at TIMESTAMPTZ,
                            completed_at TIMESTAMPTZ,
                            dead_lettered_at TIMESTAMPTZ,
                            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            PRIMARY KEY (event_id, consumer_name)
                        )
                        """
                    ).format(
                        receipts=sql.Identifier(self.receipts_table),
                        outbox=sql.Identifier(self.outbox_table),
                        sessions=sql.Identifier(self.sessions_table),
                    )
                )
                cursor.execute(
                    sql.SQL(
                        """
                        CREATE TABLE IF NOT EXISTS {agent_runs} (
                            run_id TEXT PRIMARY KEY,
                            schema_version TEXT NOT NULL,
                            correlation_id TEXT NOT NULL,
                            causation_id TEXT,
                            agent TEXT NOT NULL,
                            operation TEXT NOT NULL,
                            phase TEXT NOT NULL,
                            session_id TEXT
                                REFERENCES {sessions}(session_id)
                                ON DELETE CASCADE,
                            question_id TEXT,
                            state_version INTEGER,
                            command_id TEXT,
                            evidence_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
                            attempt_number INTEGER NOT NULL DEFAULT 1
                                CHECK (attempt_number > 0),
                            status TEXT NOT NULL
                                CHECK (status IN (
                                    'completed', 'degraded',
                                    'failed', 'cancelled'
                                )),
                            started_at TIMESTAMPTZ NOT NULL,
                            finished_at TIMESTAMPTZ NOT NULL,
                            latency_ms DOUBLE PRECISION NOT NULL
                                CHECK (latency_ms >= 0),
                            fallback_reason TEXT,
                            error_code TEXT,
                            output_type TEXT,
                            safe_metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb
                        )
                        """
                    ).format(
                        agent_runs=sql.Identifier(self.agent_runs_table),
                        sessions=sql.Identifier(self.sessions_table),
                    )
                )
                self._ensure_indexes(cursor, sql)

    def _ensure_indexes(self, cursor, sql) -> None:
        indexes = [
            (
                f"{self.outbox_table}_status_available_idx",
                self.outbox_table,
                "status, available_at",
            ),
            (
                f"{self.outbox_table}_session_idx",
                self.outbox_table,
                "session_id",
            ),
            (
                f"{self.outbox_table}_correlation_idx",
                self.outbox_table,
                "correlation_id",
            ),
            (
                f"{self.receipts_table}_status_available_idx",
                self.receipts_table,
                "status, available_at",
            ),
            (
                f"{self.receipts_table}_session_idx",
                self.receipts_table,
                "session_id",
            ),
            (
                f"{self.agent_runs_table}_session_started_idx",
                self.agent_runs_table,
                "session_id, started_at",
            ),
            (
                f"{self.agent_runs_table}_correlation_started_idx",
                self.agent_runs_table,
                "correlation_id, started_at",
            ),
            (
                f"{self.agent_runs_table}_agent_status_started_idx",
                self.agent_runs_table,
                "agent, status, started_at",
            ),
        ]
        for index_name, table_name, columns in indexes:
            cursor.execute(
                sql.SQL(
                    "CREATE INDEX IF NOT EXISTS {index} "
                    "ON {table} (" + columns + ")"
                ).format(
                    index=sql.Identifier(index_name),
                    table=sql.Identifier(table_name),
                )
            )

    @staticmethod
    def _outbox_row_to_dict(row) -> dict[str, Any] | None:
        if row is None:
            return None
        return {
            "event_id": row[0],
            "session_id": row[1],
            "correlation_id": row[2],
            "event_type": row[3],
            "schema_version": row[4],
            "payload": row[5],
            "status": row[6],
            "attempt_count": row[7],
            "max_attempts": row[8],
            "available_at": row[9],
            "lease_owner": row[10],
            "lease_expires_at": row[11],
            "last_error_code": row[12],
            "replay_count": row[13],
            "created_at": row[14],
            "updated_at": row[15],
            "published_at": row[16],
            "dead_lettered_at": row[17],
        }

    @staticmethod
    def _receipt_row_to_dict(row) -> dict[str, Any] | None:
        if row is None:
            return None
        return {
            "event_id": row[0],
            "consumer_name": row[1],
            "session_id": row[2],
            "correlation_id": row[3],
            "event_type": row[4],
            "schema_version": row[5],
            "status": row[6],
            "attempt_count": row[7],
            "max_attempts": row[8],
            "available_at": row[9],
            "lease_owner": row[10],
            "lease_expires_at": row[11],
            "last_error_code": row[12],
            "replay_count": row[13],
            "started_at": row[14],
            "completed_at": row[15],
            "dead_lettered_at": row[16],
            "created_at": row[17],
            "updated_at": row[18],
        }

    @staticmethod
    def _import_psycopg2():
        try:
            import psycopg2
            from psycopg2 import sql
        except ImportError as exc:
            raise RuntimeError(
                "psycopg2-binary is required for PostgreSQL runtime control"
            ) from exc
        return psycopg2, sql
