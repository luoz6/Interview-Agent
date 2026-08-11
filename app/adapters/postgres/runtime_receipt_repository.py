from __future__ import annotations

from datetime import datetime
from typing import Any

from app.adapters.postgres.runtime_repository_support import postgres_sql
from app.adapters.postgres.question_evaluation_repository import (
    PostgresQuestionEvaluationRepository,
)
from app.services.postgres_connections import ConnectionProvider
from app.services.question_evaluations import QuestionEvaluationRecord
from app.services.runtime_domain_events import RoundClosedEvent


_postgres_sql = postgres_sql


class PostgresRuntimeReceiptRepository:
    _COLUMNS = """
        event_id, consumer_name, session_id, correlation_id, event_type,
        schema_version, status, attempt_count, max_attempts, available_at,
        lease_owner, lease_expires_at, last_error_code, replay_count,
        started_at, completed_at, dead_lettered_at, created_at, updated_at
    """

    def __init__(
        self,
        connection_provider: ConnectionProvider,
        *,
        receipts_table: str,
        question_evaluations_table: str,
    ) -> None:
        self._connection_provider = connection_provider
        self.receipts_table = receipts_table
        self._question_evaluations = PostgresQuestionEvaluationRepository(
            connection_provider,
            question_evaluations_table=question_evaluations_table,
        )

    def reset_dead_letter(self, cursor: Any, event_id: str) -> None:
        sql = _postgres_sql()
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
            ).format(receipts=sql.Identifier(self.receipts_table)),
            (event_id,),
        )

    def claim_receipt(
        self,
        cursor: Any,
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
        sql = _postgres_sql()
        cursor.execute(
            sql.SQL(
                """
                INSERT INTO {receipts} (
                    event_id, consumer_name, session_id,
                    correlation_id, event_type, schema_version,
                    status, max_attempts
                )
                VALUES (%s, %s, %s, %s, %s, %s, 'retrying', %s)
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
                SELECT {self._COLUMNS},
                       GREATEST(0, CEIL(EXTRACT(
                           EPOCH FROM lease_expires_at - NOW()
                       )))::INTEGER AS lease_remaining_seconds,
                       GREATEST(0, CEIL(EXTRACT(
                           EPOCH FROM available_at - NOW()
                       )))::INTEGER AS retry_remaining_seconds
                FROM {{receipts}}
                WHERE event_id = %s AND consumer_name = %s
                FOR UPDATE
                """
            ).format(receipts=sql.Identifier(self.receipts_table)),
            (event.event_id, consumer_name),
        )
        row = cursor.fetchone()
        if row is None:
            raise ValueError("runtime outbox event not found")
        receipt = self.row_to_dict(row)
        if receipt["status"] == "completed":
            return {**receipt, "claim_status": "completed"}
        if receipt["status"] == "dead_letter":
            return {**receipt, "claim_status": "dead_letter"}
        if receipt["status"] == "running" and row[19] and row[19] > 0:
            return {
                **receipt,
                "claim_status": "active",
                "countdown_seconds": row[19],
            }
        if receipt["status"] == "retrying" and row[20] > 0:
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
                    lease_expires_at = NOW() + (%s * INTERVAL '1 second'),
                    started_at = COALESCE(started_at, NOW()),
                    last_error_code = NULL,
                    updated_at = NOW()
                WHERE event_id = %s AND consumer_name = %s
                RETURNING {self._COLUMNS}
                """
            ).format(receipts=sql.Identifier(self.receipts_table)),
            (worker_id, lease_seconds, event.event_id, consumer_name),
        )
        claimed = self.row_to_dict(cursor.fetchone())
        return {**claimed, "claim_status": "claimed"}

    def get_receipt(
        self,
        event_id: str,
        consumer_name: str,
    ) -> dict[str, Any] | None:
        sql = _postgres_sql()
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        f"""
                        SELECT {self._COLUMNS}
                        FROM {{receipts}}
                        WHERE event_id = %s AND consumer_name = %s
                        """
                    ).format(receipts=sql.Identifier(self.receipts_table)),
                    (event_id, consumer_name),
                )
                row = cursor.fetchone()
        return self.row_to_dict(row)

    def mark_receipt_retrying(
        self,
        cursor: Any,
        event_id: str,
        consumer_name: str,
        worker_id: str,
        *,
        error_code: str,
        available_at: datetime,
    ) -> dict[str, Any] | None:
        sql = _postgres_sql()
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
                WHERE event_id = %s AND consumer_name = %s
                  AND status = 'running' AND lease_owner = %s
                RETURNING {self._COLUMNS}
                """
            ).format(receipts=sql.Identifier(self.receipts_table)),
            (
                available_at,
                error_code,
                event_id,
                consumer_name,
                worker_id,
            ),
        )
        row = cursor.fetchone()
        return self.row_to_dict(row)

    def complete_round_review(
        self,
        cursor: Any,
        event_id: str,
        consumer_name: str,
        worker_id: str,
        record: QuestionEvaluationRecord,
    ) -> dict[str, Any]:
        return self._finish_round_review(
            cursor,
            event_id,
            consumer_name,
            worker_id,
            record,
            status="completed",
            error_code=None,
        )

    def fail_round_review(
        self,
        cursor: Any,
        event_id: str,
        consumer_name: str,
        worker_id: str,
        record: QuestionEvaluationRecord,
        *,
        error_code: str,
    ) -> dict[str, Any]:
        return self._finish_round_review(
            cursor,
            event_id,
            consumer_name,
            worker_id,
            record,
            status="dead_letter",
            error_code=error_code,
        )

    def _finish_round_review(
        self,
        cursor: Any,
        event_id: str,
        consumer_name: str,
        worker_id: str,
        record: QuestionEvaluationRecord,
        *,
        status: str,
        error_code: str | None,
    ) -> dict[str, Any]:
        sql = _postgres_sql()
        timestamp_column = (
            "completed_at" if status == "completed" else "dead_lettered_at"
        )
        self._question_evaluations.upsert_question_evaluation(cursor, record)
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
                WHERE event_id = %s AND consumer_name = %s
                  AND status = 'running' AND lease_owner = %s
                RETURNING {self._COLUMNS}
                """
            ).format(receipts=sql.Identifier(self.receipts_table)),
            (status, error_code, event_id, consumer_name, worker_id),
        )
        row = cursor.fetchone()
        if row is None:
            raise RuntimeError("runtime receipt lease was lost")
        return self.row_to_dict(row)

    @staticmethod
    def row_to_dict(row: Any) -> dict[str, Any] | None:
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


__all__ = ["PostgresRuntimeReceiptRepository"]
