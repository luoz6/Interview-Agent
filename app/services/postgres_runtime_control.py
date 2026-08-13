from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Any

from app.adapters.postgres.runtime_outbox_repository import (
    PostgresRuntimeOutboxRepository,
)
from app.adapters.postgres.runtime_receipt_repository import (
    PostgresRuntimeReceiptRepository,
)
from app.adapters.postgres.store_schema_adapter import (
    PostgresRuntimeControlSchemaAdapter,
)
from app.adapters.postgres.unit_of_work import PostgresUnitOfWork
from app.services.question_evaluations import QuestionEvaluationRecord
from app.services.agent_runtime import AgentRunRecord
from app.services.postgres_connections import (
    ConnectionProvider,
    DirectPsycopg2ConnectionProvider,
)
from app.services.postgres_identifiers import validate_runtime_table_prefix
from app.services.postgres_schema import resolve_schema_mode, validate_relations
from app.services.runtime_domain_events import RuntimeEventEnvelope
class PostgresRuntimeControlStore:
    def __init__(
        self,
        *,
        dsn: str | None = None,
        connection_provider: ConnectionProvider | None = None,
        agent_run_connection_provider: ConnectionProvider | None = None,
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
        self._agent_run_connection_provider = (
            agent_run_connection_provider or connection_provider
        )
        self.table_prefix = table_prefix
        self.sessions_table = f"{table_prefix}_sessions"
        self.question_evaluations_table = (
            f"{table_prefix}_question_evaluations"
        )
        self.outbox_table = f"{table_prefix}_runtime_outbox"
        self.receipts_table = f"{table_prefix}_runtime_event_receipts"
        self.agent_runs_table = f"{table_prefix}_agent_runs"
        self.schema_mode = resolve_schema_mode(
            schema_mode, provider_is_owned=self._provider_is_owned
        )
        if self.schema_mode == "migrate":
            PostgresRuntimeControlSchemaAdapter(
                self._connection_provider,
                table_prefix=self.table_prefix,
                sessions_table=self.sessions_table,
                outbox_table=self.outbox_table,
                receipts_table=self.receipts_table,
                agent_runs_table=self.agent_runs_table,
            ).ensure_schema()
        else:
            validate_relations(
                self._connection_provider,
                (
                    self.outbox_table,
                    self.receipts_table,
                    self.agent_runs_table,
                    f"{table_prefix}_schema_migrations",
                ),
            )
        self._receipt_repository = PostgresRuntimeReceiptRepository(
            self._connection_provider,
            receipts_table=self.receipts_table,
            question_evaluations_table=self.question_evaluations_table,
        )
        self._outbox_repository = PostgresRuntimeOutboxRepository(
            self._connection_provider,
            agent_run_connection_provider=self._agent_run_connection_provider,
            outbox_table=self.outbox_table,
            receipt_repository=self._receipt_repository,
        )

    @contextmanager
    def connection(self) -> Iterator[Any]:
        with self._connection_provider.connection() as connection:
            yield connection

    @contextmanager
    def agent_run_connection(self) -> Iterator[Any]:
        with self._agent_run_connection_provider.connection() as connection:
            yield connection

    def enqueue_event(self, cursor, event: RuntimeEventEnvelope) -> bool:
        return self._outbox_repository.enqueue_event(cursor, event)

    def count_outbox(self, event_id: str | None = None) -> int:
        return self._outbox_repository.count_outbox(event_id)

    def list_outbox(
        self,
        *,
        session_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        return self._outbox_repository.list_outbox(
            session_id=session_id,
            status=status,
            limit=limit,
        )

    def list_runtime_events(
        self,
        *,
        session_id: str,
        status: str | None = None,
        event_type: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        return self._outbox_repository.list_runtime_events(
            session_id=session_id,
            status=status,
            event_type=event_type,
            limit=limit,
        )

    def list_recovery_events(
        self,
        *,
        status: str,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        return self._outbox_repository.list_recovery_events(
            status=status,
            limit=limit,
        )

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
                    SELECT child.relname,
                           child_column.attname,
                           CASE constraint_row.confdeltype
                             WHEN 'a' THEN 'NO ACTION'
                             WHEN 'r' THEN 'RESTRICT'
                             WHEN 'c' THEN 'CASCADE'
                             WHEN 'n' THEN 'SET NULL'
                             WHEN 'd' THEN 'SET DEFAULT'
                           END
                    FROM pg_catalog.pg_constraint AS constraint_row
                    JOIN pg_catalog.pg_class AS child
                      ON child.oid = constraint_row.conrelid
                    JOIN pg_catalog.pg_namespace AS child_namespace
                      ON child_namespace.oid = child.relnamespace
                    JOIN pg_catalog.pg_class AS parent
                      ON parent.oid = constraint_row.confrelid
                    JOIN pg_catalog.pg_namespace AS parent_namespace
                      ON parent_namespace.oid = parent.relnamespace
                    JOIN LATERAL unnest(constraint_row.conkey)
                      WITH ORDINALITY AS child_key(attnum, position)
                      ON TRUE
                    JOIN LATERAL unnest(constraint_row.confkey)
                      WITH ORDINALITY AS parent_key(attnum, position)
                      ON parent_key.position = child_key.position
                    JOIN pg_catalog.pg_attribute AS child_column
                      ON child_column.attrelid = child.oid
                     AND child_column.attnum = child_key.attnum
                    WHERE constraint_row.contype = 'f'
                      AND child_namespace.nspname = current_schema()
                      AND parent_namespace.nspname = current_schema()
                      AND child.relname = ANY(%s)
                      AND parent.relname = %s
                    ORDER BY child.relname, child_key.position
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
        return self._run_mutation(
            self._outbox_repository.claim_batch,
            worker_id=worker_id,
            limit=limit,
            lease_seconds=lease_seconds,
        )

    def mark_published(
        self,
        event_id: str,
        worker_id: str,
    ) -> dict[str, Any] | None:
        return self._run_mutation(
            self._outbox_repository.mark_published,
            event_id,
            worker_id,
        )

    def mark_retrying(
        self,
        event_id: str,
        worker_id: str,
        *,
        error_code: str,
        available_at: datetime,
    ) -> dict[str, Any] | None:
        return self._run_mutation(
            self._outbox_repository.mark_retrying,
            event_id,
            worker_id,
            error_code=error_code,
            available_at=available_at,
        )

    def extend_outbox_lease(
        self,
        event_id: str,
        worker_id: str,
        lease_seconds: int,
    ) -> bool:
        return self._run_mutation(
            self._outbox_repository.extend_outbox_lease,
            event_id,
            worker_id,
            lease_seconds,
        )

    def extend_outbox_leases(
        self,
        event_ids: list[str],
        worker_id: str,
        lease_seconds: int,
    ) -> bool:
        return self._run_mutation(
            self._outbox_repository.extend_outbox_leases,
            event_ids,
            worker_id,
            lease_seconds,
        )

    def mark_dead_letter(
        self,
        event_id: str,
        worker_id: str,
        *,
        error_code: str,
    ) -> dict[str, Any] | None:
        return self._run_mutation(
            self._outbox_repository.mark_dead_letter,
            event_id,
            worker_id,
            error_code=error_code,
        )

    def release_expired_leases(self) -> int:
        return self._run_mutation(
            self._outbox_repository.release_expired_leases
        )

    def replay_dead_letter(
        self,
        event_id: str,
    ) -> dict[str, Any]:
        return self._run_mutation(
            self._outbox_repository.replay_dead_letter,
            event_id,
        )

    def record_agent_run(self, record: AgentRunRecord) -> bool:
        _, sql = self._import_psycopg2()
        with self.agent_run_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        """
                        INSERT INTO {agent_runs} (
                            run_id, schema_version, correlation_id,
                            causation_id, parent_run_id, agent, operation, phase,
                            session_id, question_id, state_version,
                            command_id, evidence_ids, attempt_number,
                            status, started_at, finished_at, latency_ms,
                            fallback_reason, error_code, output_type,
                            safe_metadata
                        )
                        VALUES (
                            %s, %s, %s, %s, %s, %s, %s, %s,
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
                        record.parent_run_id,
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
        with self.agent_run_connection() as connection:
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
                        SELECT run_id, correlation_id, causation_id, parent_run_id,
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
                "parent_run_id": row[3],
                "agent": row[4],
                "operation": row[5],
                "phase": row[6],
                "session_id": row[7],
                "question_id": row[8],
                "state_version": row[9],
                "command_id": row[10],
                "evidence_ids": row[11],
                "attempt_number": row[12],
                "status": row[13],
                "started_at": row[14],
                "finished_at": row[15],
                "latency_ms": row[16],
                "fallback_reason": row[17],
                "error_code": row[18],
                "output_type": row[19],
            }
            for row in rows
        ]

    def aggregate_agent_runs(
        self,
        *,
        started_at: datetime,
        finished_before: datetime,
        agent: str | None = None,
        operation: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        if finished_before <= started_at:
            raise ValueError("finished_before must be after started_at")
        safe_limit = max(1, min(int(limit), 100))
        _, sql = self._import_psycopg2()
        clauses = [
            sql.SQL("started_at >= %s"),
            sql.SQL("started_at < %s"),
        ]
        params: list[Any] = [started_at, finished_before]
        if agent is not None:
            clauses.append(sql.SQL("agent = %s"))
            params.append(agent)
        if operation is not None:
            clauses.append(sql.SQL("operation = %s"))
            params.append(operation)
        params.append(safe_limit)
        with self.agent_run_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        """
                        SELECT
                            agent,
                            operation,
                            COUNT(*) AS invocation_count,
                            COUNT(*) FILTER (
                                WHERE status = 'completed'
                            ) AS completed_count,
                            COUNT(*) FILTER (
                                WHERE status = 'degraded'
                            ) AS degraded_count,
                            COUNT(*) FILTER (
                                WHERE status = 'failed'
                            ) AS failed_count,
                            COUNT(*) FILTER (
                                WHERE status = 'cancelled'
                            ) AS cancelled_count,
                            COUNT(*) FILTER (
                                WHERE fallback_reason IS NOT NULL
                            ) AS fallback_count,
                            percentile_cont(0.50) WITHIN GROUP (
                                ORDER BY latency_ms
                            ) AS latency_p50_ms,
                            percentile_cont(0.95) WITHIN GROUP (
                                ORDER BY latency_ms
                            ) AS latency_p95_ms,
                            percentile_cont(0.99) WITHIN GROUP (
                                ORDER BY latency_ms
                            ) AS latency_p99_ms,
                            MIN(started_at) AS observed_from,
                            MAX(started_at) AS observed_until
                        FROM {agent_runs}
                        WHERE {where}
                        GROUP BY agent, operation
                        ORDER BY agent, operation
                        LIMIT %s
                        """
                    ).format(
                        agent_runs=sql.Identifier(self.agent_runs_table),
                        where=sql.SQL(" AND ").join(clauses),
                    ),
                    tuple(params),
                )
                rows = cursor.fetchall()
        result = []
        for row in rows:
            invocation_count = int(row[2])
            result.append(
                {
                    "agent": row[0],
                    "operation": row[1],
                    "invocation_count": invocation_count,
                    "completed_count": int(row[3]),
                    "degraded_count": int(row[4]),
                    "failed_count": int(row[5]),
                    "cancelled_count": int(row[6]),
                    "fallback_count": int(row[7]),
                    "completed_rate": int(row[3]) / invocation_count,
                    "degraded_rate": int(row[4]) / invocation_count,
                    "failed_rate": int(row[5]) / invocation_count,
                    "cancelled_rate": int(row[6]) / invocation_count,
                    "fallback_rate": int(row[7]) / invocation_count,
                    "latency_p50_ms": float(row[8]),
                    "latency_p95_ms": float(row[9]),
                    "latency_p99_ms": float(row[10]),
                    "observed_from": row[11],
                    "observed_until": row[12],
                }
            )
        return result

    def claim_receipt(
        self,
        event: RoundClosedEvent,
        *,
        consumer_name: str,
        worker_id: str,
        lease_seconds: int,
        max_attempts: int = 5,
    ) -> dict[str, Any]:
        return self._run_mutation(
            self._receipt_repository.claim_receipt,
            event,
            consumer_name=consumer_name,
            worker_id=worker_id,
            lease_seconds=lease_seconds,
            max_attempts=max_attempts,
        )

    def get_receipt(
        self,
        event_id: str,
        consumer_name: str,
    ) -> dict[str, Any] | None:
        return self._receipt_repository.get_receipt(event_id, consumer_name)

    def mark_receipt_retrying(
        self,
        event_id: str,
        consumer_name: str,
        worker_id: str,
        *,
        error_code: str,
        available_at: datetime,
    ) -> dict[str, Any] | None:
        return self._run_mutation(
            self._receipt_repository.mark_receipt_retrying,
            event_id,
            consumer_name,
            worker_id,
            error_code=error_code,
            available_at=available_at,
        )

    def complete_round_review(
        self,
        event_id: str,
        consumer_name: str,
        worker_id: str,
        record: QuestionEvaluationRecord,
    ) -> dict[str, Any]:
        return self._run_mutation(
            self._receipt_repository.complete_round_review,
            event_id,
            consumer_name,
            worker_id,
            record,
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
        return self._run_mutation(
            self._receipt_repository.fail_round_review,
            event_id,
            consumer_name,
            worker_id,
            record,
            error_code=error_code,
        )

    def _run_mutation(self, operation, *args, **kwargs):
        with PostgresUnitOfWork(self._connection_provider) as unit_of_work:
            result = operation(unit_of_work.cursor, *args, **kwargs)
            unit_of_work.commit()
        return result


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
