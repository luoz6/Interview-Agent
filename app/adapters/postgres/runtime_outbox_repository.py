from __future__ import annotations

from datetime import datetime
from typing import Any

from app.adapters.postgres.runtime_receipt_repository import (
    PostgresRuntimeReceiptRepository,
)
from app.adapters.postgres.runtime_repository_support import postgres_sql
from app.services.postgres_connections import ConnectionProvider
from app.services.runtime_domain_events import RuntimeEventEnvelope


_postgres_sql = postgres_sql


class PostgresRuntimeOutboxRepository:
    _COLUMNS = """
        event_id, session_id, correlation_id, event_type, schema_version,
        payload_json, status, attempt_count, max_attempts, available_at,
        lease_owner, lease_expires_at, last_error_code, replay_count,
        created_at, updated_at, published_at, dead_lettered_at
    """

    def __init__(
        self,
        connection_provider: ConnectionProvider,
        *,
        agent_run_connection_provider: ConnectionProvider,
        outbox_table: str,
        receipt_repository: PostgresRuntimeReceiptRepository,
    ) -> None:
        self._connection_provider = connection_provider
        self._agent_run_connection_provider = agent_run_connection_provider
        self.outbox_table = outbox_table
        self._receipts = receipt_repository

    def enqueue_event(self, cursor: Any, event: RuntimeEventEnvelope) -> bool:
        sql = _postgres_sql()
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
        sql = _postgres_sql()
        statement = sql.SQL("SELECT COUNT(*) FROM {outbox}").format(
            outbox=sql.Identifier(self.outbox_table)
        )
        params: tuple[Any, ...] = ()
        if event_id is not None:
            statement += sql.SQL(" WHERE event_id = %s")
            params = (event_id,)
        with self._connection_provider.connection() as connection:
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
        sql = _postgres_sql()
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
        with self._agent_run_connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        f"""
                        SELECT {self._COLUMNS}
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
        return [self.row_to_dict(row) for row in rows]

    def list_runtime_events(
        self,
        *,
        session_id: str,
        status: str | None = None,
        event_type: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        sql = _postgres_sql()
        clauses = [sql.SQL("session_id = %s")]
        params: list[Any] = [session_id]
        if status is not None:
            clauses.append(sql.SQL("status = %s"))
            params.append(status)
        if event_type is not None:
            clauses.append(sql.SQL("event_type = %s"))
            params.append(event_type)
        params.append(limit)
        with self._connection_provider.connection() as connection:
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
        sql = _postgres_sql()
        with self._connection_provider.connection() as connection:
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

    def claim_batch(
        self,
        cursor: Any,
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
        sql = _postgres_sql()
        cursor.execute(
            sql.SQL(
                """
                SELECT event_id
                FROM {outbox}
                WHERE (
                    (status IN ('pending', 'retrying') AND available_at <= NOW())
                    OR (status = 'running' AND lease_expires_at <= NOW())
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
                    lease_expires_at = NOW() + (%s * INTERVAL '1 second'),
                    last_error_code = NULL,
                    updated_at = NOW()
                WHERE event_id = ANY(%s)
                RETURNING {self._COLUMNS}
                """
            ).format(outbox=sql.Identifier(self.outbox_table)),
            (worker_id, lease_seconds, event_ids),
        )
        rows = cursor.fetchall()
        by_id = {row[0]: self.row_to_dict(row) for row in rows}
        return [by_id[event_id] for event_id in event_ids]

    def mark_published(
        self,
        cursor: Any,
        event_id: str,
        worker_id: str,
    ) -> dict[str, Any] | None:
        sql = _postgres_sql()
        return self._update_returning(
            cursor,
            sql.SQL(
                f"""
                UPDATE {{outbox}}
                SET status = 'published',
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    last_error_code = NULL,
                    published_at = NOW(),
                    updated_at = NOW()
                WHERE event_id = %s AND status = 'running' AND lease_owner = %s
                RETURNING {self._COLUMNS}
                """
            ).format(outbox=sql.Identifier(self.outbox_table)),
            (event_id, worker_id),
        )

    def mark_retrying(
        self,
        cursor: Any,
        event_id: str,
        worker_id: str,
        *,
        error_code: str,
        available_at: datetime,
    ) -> dict[str, Any] | None:
        sql = _postgres_sql()
        return self._update_returning(
            cursor,
            sql.SQL(
                f"""
                UPDATE {{outbox}}
                SET status = 'retrying',
                    available_at = %s,
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    last_error_code = %s,
                    updated_at = NOW()
                WHERE event_id = %s AND status = 'running' AND lease_owner = %s
                RETURNING {self._COLUMNS}
                """
            ).format(outbox=sql.Identifier(self.outbox_table)),
            (available_at, error_code, event_id, worker_id),
        )

    def extend_outbox_lease(
        self,
        cursor: Any,
        event_id: str,
        worker_id: str,
        lease_seconds: int,
    ) -> bool:
        sql = _postgres_sql()
        cursor.execute(
            sql.SQL(
                """
                UPDATE {outbox}
                SET lease_expires_at = NOW() + (%s * INTERVAL '1 second'),
                    updated_at = NOW()
                WHERE event_id = %s AND status = 'running' AND lease_owner = %s
                """
            ).format(outbox=sql.Identifier(self.outbox_table)),
            (lease_seconds, event_id, worker_id),
        )
        return cursor.rowcount == 1

    def extend_outbox_leases(
        self,
        cursor: Any,
        event_ids: list[str],
        worker_id: str,
        lease_seconds: int,
    ) -> bool:
        if not event_ids:
            return True
        sql = _postgres_sql()
        cursor.execute(
            sql.SQL(
                """
                UPDATE {outbox}
                SET lease_expires_at = NOW() + (%s * INTERVAL '1 second'),
                    updated_at = NOW()
                WHERE event_id = ANY(%s)
                  AND status = 'running' AND lease_owner = %s
                """
            ).format(outbox=sql.Identifier(self.outbox_table)),
            (lease_seconds, event_ids, worker_id),
        )
        return cursor.rowcount == len(event_ids)

    def mark_dead_letter(
        self,
        cursor: Any,
        event_id: str,
        worker_id: str,
        *,
        error_code: str,
    ) -> dict[str, Any] | None:
        sql = _postgres_sql()
        return self._update_returning(
            cursor,
            sql.SQL(
                f"""
                UPDATE {{outbox}}
                SET status = 'dead_letter',
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    last_error_code = %s,
                    dead_lettered_at = NOW(),
                    updated_at = NOW()
                WHERE event_id = %s AND status = 'running' AND lease_owner = %s
                RETURNING {self._COLUMNS}
                """
            ).format(outbox=sql.Identifier(self.outbox_table)),
            (error_code, event_id, worker_id),
        )

    def release_expired_leases(self, cursor: Any) -> int:
        sql = _postgres_sql()
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
                WHERE status = 'running' AND lease_expires_at <= NOW()
                """
            ).format(outbox=sql.Identifier(self.outbox_table))
        )
        count = cursor.rowcount
        return int(count)

    def replay_dead_letter(self, cursor: Any, event_id: str) -> dict[str, Any]:
        sql = _postgres_sql()
        cursor.execute(
            sql.SQL(
                f"""
                SELECT {self._COLUMNS}
                FROM {{outbox}}
                WHERE event_id = %s
                FOR UPDATE
                """
            ).format(outbox=sql.Identifier(self.outbox_table)),
            (event_id,),
        )
        existing = self.row_to_dict(cursor.fetchone())
        if existing is None or existing["status"] != "dead_letter":
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
                RETURNING {self._COLUMNS}
                """
            ).format(outbox=sql.Identifier(self.outbox_table)),
            (event_id,),
        )
        replayed = self.row_to_dict(cursor.fetchone())
        self._receipts.reset_dead_letter(cursor, event_id)
        return replayed

    def _update_returning(
        self,
        cursor: Any,
        statement: Any,
        params: tuple[Any, ...],
    ) -> dict[str, Any] | None:
        cursor.execute(statement, params)
        row = cursor.fetchone()
        return self.row_to_dict(row)

    @staticmethod
    def row_to_dict(row: Any) -> dict[str, Any] | None:
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


__all__ = ["PostgresRuntimeOutboxRepository"]
