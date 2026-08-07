import json
import hashlib
from typing import Literal
from uuid import uuid4

from app.services.config import (
    get_report_langgraph_rollout_percent,
    get_report_langgraph_runtime_enabled,
    get_report_langgraph_version,
    get_runtime_store,
)
from app.services.postgres_connections import (
    ConnectionProvider,
    DirectPsycopg2ConnectionProvider,
)
from app.services.postgres_identifiers import (
    runtime_schema_identifier,
    validate_runtime_table_prefix,
)
from app.services.postgres_schema import resolve_schema_mode, validate_relations


ReviewWorkflowEngine = Literal["legacy", "langgraph-review-v1"]


def choose_report_workflow_engine(
    job_id: str,
    *,
    runtime_store: str,
    runtime_enabled: bool,
    rollout_percent: int,
) -> ReviewWorkflowEngine:
    if runtime_store != "postgres" or not runtime_enabled or rollout_percent == 0:
        return "legacy"
    bucket = int(hashlib.sha256(job_id.encode("utf-8")).hexdigest()[:8], 16) % 100
    return "langgraph-review-v1" if bucket < rollout_percent else "legacy"


class PostgresReportJobStore:
    def __init__(
        self,
        *,
        dsn: str | None = None,
        connection_provider: ConnectionProvider | None = None,
        table_prefix: str = "interview",
        lease_seconds: int = 300,
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
        self.lease_seconds = lease_seconds
        self.sessions_table = f"{table_prefix}_sessions"
        self.reports_table = f"{table_prefix}_reports"
        self.jobs_table = f"{table_prefix}_report_jobs"
        self.schema_mode = resolve_schema_mode(
            schema_mode, provider_is_owned=self._provider_is_owned
        )
        if self.schema_mode == "migrate":
            self._ensure_schema()
        else:
            validate_relations(
                self._connection_provider,
                (
                    self.sessions_table,
                    self.reports_table,
                    self.jobs_table,
                    f"{table_prefix}_schema_migrations",
                ),
            )

    def drop_tables(self) -> None:
        psycopg2, sql = self._import_psycopg2()
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                for table_name in (
                    f"{self.table_prefix}_report_heads",
                    f"{self.table_prefix}_report_artifacts",
                ):
                    cursor.execute(
                        sql.SQL("DROP TABLE IF EXISTS {table}").format(
                            table=sql.Identifier(table_name)
                        )
                    )
                cursor.execute(
                    sql.SQL("DROP TABLE IF EXISTS {jobs}").format(
                        jobs=sql.Identifier(self.jobs_table)
                    )
                )
                cursor.execute(
                    sql.SQL("DROP TABLE IF EXISTS {reports}").format(
                        reports=sql.Identifier(self.reports_table)
                    )
                )

    def count_jobs(self) -> int:
        return self._count_rows(self.jobs_table)

    def count_reports(self) -> int:
        return self._count_rows(self.reports_table)

    def get_job_by_session(self, session_id: str) -> dict | None:
        _, sql = self._import_psycopg2()
        row = self._fetchone(
            sql.SQL(
                """
                SELECT job_id, session_id, status, lease_owner, lease_expires_at,
                       attempt_count, max_attempts, last_error,
                       last_error_code, replay_count, review_engine,
                       review_graph_schema_version, queued_at,
                       started_at, finished_at, updated_at, lease_token,
                       available_at, scheduled_attempt, heartbeat_at
                FROM {jobs}
                WHERE session_id = %s
                """
            ).format(jobs=sql.Identifier(self.jobs_table)),
            (session_id,),
        )
        return self._job_row_to_dict(row)

    def get_job(self, job_id: str) -> dict | None:
        _, sql = self._import_psycopg2()
        row = self._fetchone(
            sql.SQL(
                """
                SELECT job_id, session_id, status, lease_owner, lease_expires_at,
                       attempt_count, max_attempts, last_error,
                       last_error_code, replay_count, review_engine,
                       review_graph_schema_version, queued_at,
                       started_at, finished_at, updated_at, lease_token,
                       available_at, scheduled_attempt, heartbeat_at
                FROM {jobs}
                WHERE job_id = %s::uuid
                """
            ).format(jobs=sql.Identifier(self.jobs_table)),
            (job_id,),
        )
        return self._job_row_to_dict(row)

    def get_report_row(self, session_id: str) -> dict | None:
        _, sql = self._import_psycopg2()
        row = self._fetchone(
            sql.SQL(
                """
                SELECT session_id, status, created_at, updated_at
                FROM {reports}
                WHERE session_id = %s
                """
            ).format(reports=sql.Identifier(self.reports_table)),
            (session_id,),
        )
        if row is None:
            return None
        return {
            "session_id": row[0],
            "status": row[1],
            "created_at": row[2],
            "updated_at": row[3],
        }

    def enqueue_report_request(self, session_id: str) -> dict:
        psycopg2, sql = self._import_psycopg2()
        job_id = str(uuid4())
        review_engine = choose_report_workflow_engine(
            job_id,
            runtime_store=get_runtime_store(),
            runtime_enabled=get_report_langgraph_runtime_enabled(),
            rollout_percent=get_report_langgraph_rollout_percent(),
        )
        review_graph_schema_version = (
            get_report_langgraph_version()
            if review_engine == "langgraph-review-v1"
            else None
        )
        progress_json = json.dumps(self._processing_progress_payload(), ensure_ascii=False)
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                # The session row serializes enqueue-or-get for this session.
                cursor.execute(
                    sql.SQL(
                        "SELECT deletion_status FROM {sessions} "
                        "WHERE session_id = %s FOR UPDATE"
                    ).format(sessions=sql.Identifier(self.sessions_table)),
                    (session_id,),
                )
                session = cursor.fetchone()
                if session is None:
                    raise ValueError("session not found")
                if session[0] != "active":
                    raise ValueError("session is deleting")
                cursor.execute(
                    sql.SQL(
                        """
                        SELECT job_id, session_id, status, lease_owner, lease_expires_at,
                               attempt_count, max_attempts, last_error,
                               last_error_code, replay_count, review_engine,
                               review_graph_schema_version, queued_at,
                               started_at, finished_at, updated_at, lease_token
                        FROM {jobs}
                        WHERE session_id = %s
                        """
                    ).format(jobs=sql.Identifier(self.jobs_table)),
                    (session_id,),
                )
                existing = cursor.fetchone()
                if existing is not None:
                    return self._job_row_to_dict(existing)
                cursor.execute(
                    sql.SQL(
                        """
                        INSERT INTO {reports} (session_id, status, progress_json)
                        VALUES (%s, 'processing', %s::jsonb)
                        ON CONFLICT (session_id) DO UPDATE
                        SET status = 'processing',
                            progress_json = EXCLUDED.progress_json,
                            report_json = NULL,
                            error = NULL,
                            updated_at = NOW()
                        """
                    ).format(reports=sql.Identifier(self.reports_table)),
                    (session_id, progress_json),
                )
                cursor.execute(
                    sql.SQL(
                        """
                        INSERT INTO {jobs} (
                            job_id, session_id, status, attempt_count,
                            max_attempts, review_engine,
                            review_graph_schema_version, job_kind,
                            activate_on_success, idempotency_key
                        )
                        VALUES (%s::uuid, %s, 'queued', 0, 3, %s, %s, %s, %s, %s)
                        RETURNING job_id, session_id, status, lease_owner, lease_expires_at,
                                  attempt_count, max_attempts, last_error,
                                  last_error_code, replay_count, review_engine,
                                  review_graph_schema_version, queued_at,
                                  started_at, finished_at, updated_at, lease_token,
                                  available_at, scheduled_attempt, heartbeat_at
                        """
                    ).format(jobs=sql.Identifier(self.jobs_table)),
                    (
                        job_id,
                        session_id,
                        review_engine,
                        review_graph_schema_version,
                        "initial",
                        True,
                        f"legacy-request:{session_id}",
                    ),
                )
                row = cursor.fetchone()
                heads_table = f"{self.table_prefix}_report_heads"
                cursor.execute("SELECT to_regclass(%s)", (f"public.{heads_table}",))
                if cursor.fetchone()[0] is not None:
                    cursor.execute(
                        sql.SQL(
                            "INSERT INTO {heads}(session_id,latest_job_id,updated_at) "
                            "VALUES(%s,%s::uuid,NOW()) ON CONFLICT(session_id) DO UPDATE "
                            "SET latest_job_id=EXCLUDED.latest_job_id,updated_at=NOW()"
                        ).format(heads=sql.Identifier(heads_table)),
                        (session_id, job_id),
                    )
        return self._job_row_to_dict(row)

    def claim_next(self, worker_id: str, lease_seconds: int | None = None) -> dict | None:
        psycopg2, sql = self._import_psycopg2()
        lease_duration = self.lease_seconds if lease_seconds is None else lease_seconds
        lease_token = str(uuid4())
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        """
                        WITH next_job AS (
                            SELECT job_id
                            FROM {jobs}
                            WHERE (status IN ('queued', 'retrying')
                                   AND available_at <= NOW())
                               OR (status = 'running' AND lease_expires_at <= NOW())
                            ORDER BY available_at, queued_at
                            FOR UPDATE SKIP LOCKED
                            LIMIT 1
                        )
                        UPDATE {jobs} AS jobs
                        SET status = 'running',
                            lease_owner = %s,
                            lease_token = %s::uuid,
                            lease_expires_at = NOW() + (%s * INTERVAL '1 second'),
                            heartbeat_at = NOW(),
                            started_at = COALESCE(jobs.started_at, NOW()),
                            updated_at = NOW()
                        FROM next_job
                        WHERE jobs.job_id = next_job.job_id
                        RETURNING jobs.job_id, jobs.session_id, jobs.status, jobs.lease_owner,
                                  jobs.lease_expires_at, jobs.attempt_count, jobs.max_attempts,
                                  jobs.last_error, jobs.last_error_code,
                                  jobs.replay_count, jobs.review_engine,
                                  jobs.review_graph_schema_version, jobs.queued_at,
                                  jobs.started_at,
                                  jobs.finished_at, jobs.updated_at,
                                  jobs.lease_token, jobs.available_at,
                                  jobs.scheduled_attempt, jobs.heartbeat_at
                        """
                    ).format(jobs=sql.Identifier(self.jobs_table)),
                    (worker_id, lease_token, lease_duration),
                )
                row = cursor.fetchone()
        return self._job_row_to_dict(row)

    def schedule_review_retry(
        self,
        job_id: str,
        *,
        next_attempt_number: int,
        delay_seconds: float = 0,
    ) -> str:
        if next_attempt_number < 1:
            raise ValueError("next_attempt_number must be positive")
        if delay_seconds < 0:
            raise ValueError("delay_seconds must be non-negative")
        psycopg2, sql = self._import_psycopg2()
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        """
                        SELECT status, review_engine, scheduled_attempt
                        FROM {jobs}
                        WHERE job_id = %s::uuid
                        FOR UPDATE
                        """
                    ).format(jobs=sql.Identifier(self.jobs_table)),
                    (job_id,),
                )
                current = cursor.fetchone()
                if current is None or current[1] != "langgraph-review-v1":
                    return "discarded_stale_retry"
                if current[0] in {"completed", "failed"}:
                    return "discarded_stale_retry"
                scheduled = current[2]
                if scheduled is not None:
                    scheduled = int(scheduled)
                    if scheduled > next_attempt_number:
                        return "discarded_stale_retry"
                    if scheduled == next_attempt_number:
                        return "scheduled"
                cursor.execute(
                    sql.SQL(
                        """
                        UPDATE {jobs}
                        SET status = 'retrying',
                            scheduled_attempt = %s,
                            available_at = NOW() + (%s * INTERVAL '1 second'),
                            lease_owner = NULL,
                            lease_token = NULL,
                            lease_expires_at = NULL,
                            updated_at = NOW()
                        WHERE job_id = %s::uuid
                        """
                    ).format(jobs=sql.Identifier(self.jobs_table)),
                    (next_attempt_number, delay_seconds, job_id),
                )
        return "scheduled"

    def release_claim_for_retry(
        self,
        job_id: str,
        *,
        worker_id: str,
        lease_token: str,
        delay_seconds: float = 0.25,
    ) -> bool:
        psycopg2, sql = self._import_psycopg2()
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        """
                        UPDATE {jobs}
                        SET status = 'retrying',
                            available_at = NOW() + (%s * INTERVAL '1 second'),
                            lease_owner = NULL,
                            lease_token = NULL,
                            lease_expires_at = NULL,
                            updated_at = NOW()
                        WHERE job_id = %s::uuid
                          AND status = 'running'
                          AND lease_owner = %s
                          AND lease_token = %s::uuid
                          AND lease_expires_at > NOW()
                        """
                    ).format(jobs=sql.Identifier(self.jobs_table)),
                    (delay_seconds, job_id, worker_id, lease_token),
                )
                return cursor.rowcount == 1

    def assert_lease(
        self, job_id: str, *, worker_id: str, lease_token: str
    ) -> bool:
        _, sql = self._import_psycopg2()
        row = self._fetchone(
            sql.SQL(
                """
                SELECT EXISTS (
                    SELECT 1 FROM {jobs}
                    WHERE job_id = %s::uuid
                      AND status = 'running'
                      AND lease_owner = %s
                      AND lease_token = %s::uuid
                      AND lease_expires_at > NOW()
                )
                """
            ).format(jobs=sql.Identifier(self.jobs_table)),
            (job_id, worker_id, lease_token),
        )
        return bool(row and row[0])

    def heartbeat(
        self,
        job_id: str,
        *,
        worker_id: str,
        lease_token: str,
        lease_seconds: int | None = None,
    ) -> bool:
        psycopg2, sql = self._import_psycopg2()
        duration = self.lease_seconds if lease_seconds is None else lease_seconds
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        """
                        UPDATE {jobs}
                        SET lease_expires_at =
                                NOW() + (%s * INTERVAL '1 second'),
                            heartbeat_at = NOW(),
                            updated_at = NOW()
                        WHERE job_id = %s::uuid
                          AND status = 'running'
                          AND lease_owner = %s
                          AND lease_token = %s::uuid
                          AND lease_expires_at > NOW()
                        """
                    ).format(jobs=sql.Identifier(self.jobs_table)),
                    (duration, job_id, worker_id, lease_token),
                )
                return cursor.rowcount == 1

    def mark_completed(
        self,
        job_id: str,
        *,
        worker_id: str | None = None,
        lease_token: str | None = None,
    ) -> dict | None:
        psycopg2, sql = self._import_psycopg2()
        lease_guard, lease_params = self._terminal_lease_guard(
            sql,
            worker_id=worker_id,
            lease_token=lease_token,
        )
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        """
                        UPDATE {jobs}
                        SET status = 'completed',
                            lease_owner = NULL,
                            lease_token = NULL,
                            lease_expires_at = NULL,
                            finished_at = NOW(),
                            updated_at = NOW()
                        WHERE job_id = %s::uuid
                        {lease_guard}
                        RETURNING job_id, session_id, status, lease_owner, lease_expires_at,
                                  attempt_count, max_attempts, last_error,
                                  last_error_code, replay_count, review_engine,
                                  review_graph_schema_version, queued_at,
                                  started_at, finished_at, updated_at
                        """
                    ).format(
                        jobs=sql.Identifier(self.jobs_table),
                        lease_guard=lease_guard,
                    ),
                    (job_id, *lease_params),
                )
                row = cursor.fetchone()
        return self._job_row_to_dict(row)

    def mark_retryable_failure(
        self,
        job_id: str,
        error: str,
        *,
        error_code: str = "unexpected_error",
        worker_id: str | None = None,
        lease_token: str | None = None,
    ) -> dict | None:
        psycopg2, sql = self._import_psycopg2()
        lease_guard, lease_params = self._terminal_lease_guard(
            sql,
            worker_id=worker_id,
            lease_token=lease_token,
        )
        progress_json = json.dumps(self._processing_progress_payload(), ensure_ascii=False)
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        """
                        WITH updated_job AS (
                            UPDATE {jobs}
                            SET attempt_count = attempt_count + 1,
                                status = CASE
                                    WHEN attempt_count + 1 >= max_attempts THEN 'failed'
                                    ELSE 'retrying'
                                END,
                                last_error = %s,
                                last_error_code = %s,
                                lease_owner = NULL,
                                lease_token = NULL,
                                lease_expires_at = NULL,
                                finished_at = CASE
                                    WHEN attempt_count + 1 >= max_attempts THEN NOW()
                                    ELSE finished_at
                                END,
                                updated_at = NOW()
                            WHERE job_id = %s::uuid
                            {lease_guard}
                            RETURNING job_id, session_id, status, lease_owner, lease_expires_at,
                                      attempt_count, max_attempts, last_error,
                                      last_error_code, replay_count, review_engine,
                                      review_graph_schema_version, queued_at,
                                      started_at, finished_at, updated_at
                        )
                        UPDATE {reports} AS reports
                        SET status = CASE
                                WHEN updated_job.status = 'failed' THEN 'failed'
                                ELSE 'processing'
                            END,
                            progress_json = CASE
                                WHEN updated_job.status = 'failed' THEN NULL
                                ELSE %s::jsonb
                            END,
                            report_json = NULL,
                            error = CASE
                                WHEN updated_job.status = 'failed' THEN %s
                                ELSE NULL
                            END,
                            updated_at = NOW()
                        FROM updated_job
                        WHERE reports.session_id = updated_job.session_id
                        RETURNING updated_job.job_id, updated_job.session_id, updated_job.status,
                                  updated_job.lease_owner, updated_job.lease_expires_at,
                                  updated_job.attempt_count, updated_job.max_attempts,
                                  updated_job.last_error,
                                  updated_job.last_error_code,
                                  updated_job.replay_count,
                                  updated_job.review_engine,
                                  updated_job.review_graph_schema_version,
                                  updated_job.queued_at,
                                  updated_job.started_at, updated_job.finished_at,
                                  updated_job.updated_at
                        """
                    ).format(
                        jobs=sql.Identifier(self.jobs_table),
                        reports=sql.Identifier(self.reports_table),
                        lease_guard=lease_guard,
                    ),
                    (
                        error,
                        error_code,
                        job_id,
                        *lease_params,
                        progress_json,
                        error,
                    ),
                )
                row = cursor.fetchone()
        return self._job_row_to_dict(row)

    def mark_failed(
        self,
        job_id: str,
        error: str,
        *,
        error_code: str = "unexpected_error",
        worker_id: str | None = None,
        lease_token: str | None = None,
    ) -> dict | None:
        psycopg2, sql = self._import_psycopg2()
        lease_guard, lease_params = self._terminal_lease_guard(
            sql,
            worker_id=worker_id,
            lease_token=lease_token,
        )
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        """
                        WITH updated_job AS (
                            UPDATE {jobs}
                            SET status = 'failed',
                                last_error = %s,
                                last_error_code = %s,
                                lease_owner = NULL,
                                lease_token = NULL,
                                lease_expires_at = NULL,
                                finished_at = NOW(),
                                updated_at = NOW()
                            WHERE job_id = %s::uuid
                            {lease_guard}
                            RETURNING job_id, session_id, status, lease_owner, lease_expires_at,
                                      attempt_count, max_attempts, last_error,
                                      last_error_code, replay_count, review_engine,
                                      review_graph_schema_version, queued_at,
                                      started_at, finished_at, updated_at
                        )
                        UPDATE {reports} AS reports
                        SET status = 'failed',
                            progress_json = NULL,
                            report_json = NULL,
                            error = %s,
                            failed_at = NOW(),
                            updated_at = NOW()
                        FROM updated_job
                        WHERE reports.session_id = updated_job.session_id
                        RETURNING updated_job.job_id, updated_job.session_id, updated_job.status,
                                  updated_job.lease_owner, updated_job.lease_expires_at,
                                  updated_job.attempt_count, updated_job.max_attempts,
                                  updated_job.last_error,
                                  updated_job.last_error_code,
                                  updated_job.replay_count,
                                  updated_job.review_engine,
                                  updated_job.review_graph_schema_version,
                                  updated_job.queued_at,
                                  updated_job.started_at, updated_job.finished_at,
                                  updated_job.updated_at
                        """
                    ).format(
                        jobs=sql.Identifier(self.jobs_table),
                        reports=sql.Identifier(self.reports_table),
                        lease_guard=lease_guard,
                    ),
                    (error, error_code, job_id, *lease_params, error),
                )
                row = cursor.fetchone()
        return self._job_row_to_dict(row)

    @staticmethod
    def _terminal_lease_guard(sql, *, worker_id, lease_token):
        if (worker_id is None) != (lease_token is None):
            raise ValueError("worker_id and lease_token must be provided together")
        if worker_id is None:
            return sql.SQL(""), ()
        return (
            sql.SQL(
                "AND status = 'running' "
                "AND lease_owner = %s "
                "AND lease_token = %s::uuid "
                "AND lease_expires_at > NOW()"
            ),
            (worker_id, lease_token),
        )

    def requeue_failed(self, session_id: str) -> dict:
        psycopg2, sql = self._import_psycopg2()
        progress_json = json.dumps(
            self._processing_progress_payload(),
            ensure_ascii=False,
        )
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        """
                        WITH requeued AS (
                            UPDATE {jobs}
                            SET status = 'queued',
                                lease_owner = NULL,
                                lease_token = NULL,
                                lease_expires_at = NULL,
                                heartbeat_at = NULL,
                                attempt_count = 0,
                                last_error = NULL,
                                last_error_code = NULL,
                                replay_count = replay_count + 1,
                                started_at = NULL,
                                finished_at = NULL,
                                updated_at = NOW()
                            WHERE session_id = %s
                              AND status = 'failed'
                            RETURNING job_id, session_id, status,
                                      lease_owner, lease_expires_at,
                                      attempt_count, max_attempts,
                                      last_error, last_error_code,
                                      replay_count, review_engine,
                                      review_graph_schema_version, queued_at, started_at,
                                      finished_at, updated_at
                        )
                        UPDATE {reports} AS reports
                        SET status = 'processing',
                            progress_json = %s::jsonb,
                            report_json = NULL,
                            error = NULL,
                            completed_at = NULL,
                            failed_at = NULL,
                            updated_at = NOW()
                        FROM requeued
                        WHERE reports.session_id = requeued.session_id
                        RETURNING requeued.job_id, requeued.session_id,
                                  requeued.status, requeued.lease_owner,
                                  requeued.lease_expires_at,
                                  requeued.attempt_count,
                                  requeued.max_attempts,
                                  requeued.last_error,
                                  requeued.last_error_code,
                                  requeued.replay_count,
                                  requeued.review_engine,
                                  requeued.review_graph_schema_version,
                                  requeued.queued_at,
                                  requeued.started_at,
                                  requeued.finished_at,
                                  requeued.updated_at
                        """
                    ).format(
                        jobs=sql.Identifier(self.jobs_table),
                        reports=sql.Identifier(self.reports_table),
                    ),
                    (session_id, progress_json),
                )
                row = cursor.fetchone()
                if row is None:
                    raise ValueError("report job is not failed")
        return self._job_row_to_dict(row)

    def insert_processing_report_only(self, session_id: str) -> None:
        psycopg2, sql = self._import_psycopg2()
        progress_json = json.dumps(self._processing_progress_payload(), ensure_ascii=False)
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        """
                        INSERT INTO {reports} (session_id, status, progress_json)
                        VALUES (%s, 'processing', %s::jsonb)
                        ON CONFLICT (session_id) DO UPDATE
                        SET status = 'processing',
                            progress_json = EXCLUDED.progress_json,
                            report_json = NULL,
                            error = NULL,
                            updated_at = NOW()
                        """
                    ).format(reports=sql.Identifier(self.reports_table)),
                    (session_id, progress_json),
                )

    def repair_orphan_processing_reports(self) -> int:
        psycopg2, sql = self._import_psycopg2()
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        """
                        SELECT reports.session_id
                        FROM {reports} AS reports
                        LEFT JOIN {jobs} AS jobs
                            ON jobs.session_id = reports.session_id
                        WHERE reports.status = 'processing'
                          AND jobs.session_id IS NULL
                        """
                    ).format(
                        jobs=sql.Identifier(self.jobs_table),
                        reports=sql.Identifier(self.reports_table),
                    )
                )
                missing_session_ids = [row[0] for row in cursor.fetchall()]
                for session_id in missing_session_ids:
                    cursor.execute(
                        sql.SQL(
                            """
                            INSERT INTO {jobs} (
                                job_id, session_id, status, attempt_count, max_attempts
                            )
                            VALUES (%s::uuid, %s, 'queued', 0, 3)
                            """
                        ).format(jobs=sql.Identifier(self.jobs_table)),
                        (str(uuid4()), session_id),
                    )
                return len(missing_session_ids)

    def _ensure_schema(self) -> None:
        psycopg2, sql = self._import_psycopg2()
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        """
                        CREATE TABLE IF NOT EXISTS {reports} (
                            session_id TEXT PRIMARY KEY
                                REFERENCES {sessions}(session_id) ON DELETE CASCADE,
                            status TEXT NOT NULL CHECK (status IN ('processing', 'completed', 'failed')),
                            progress_json JSONB,
                            report_json JSONB,
                            error TEXT,
                            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            completed_at TIMESTAMPTZ,
                            failed_at TIMESTAMPTZ
                        )
                        """
                    ).format(
                        reports=sql.Identifier(self.reports_table),
                        sessions=sql.Identifier(self.sessions_table),
                    )
                )
                cursor.execute(
                    sql.SQL(
                        """
                        CREATE TABLE IF NOT EXISTS {jobs} (
                            job_id UUID PRIMARY KEY,
                            session_id TEXT NOT NULL UNIQUE
                                REFERENCES {sessions}(session_id) ON DELETE CASCADE,
                            status TEXT NOT NULL CHECK (
                                status IN ('queued', 'running', 'retrying', 'completed', 'failed')
                            ),
                            lease_owner TEXT,
                            lease_token UUID,
                            lease_expires_at TIMESTAMPTZ,
                            heartbeat_at TIMESTAMPTZ,
                            available_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            scheduled_attempt INTEGER,
                            attempt_count INTEGER NOT NULL DEFAULT 0,
                            max_attempts INTEGER NOT NULL DEFAULT 3,
                            last_error TEXT,
                            last_error_code TEXT,
                            replay_count INTEGER NOT NULL DEFAULT 0,
                            review_engine TEXT NOT NULL DEFAULT 'legacy'
                                CHECK (review_engine IN ('legacy', 'langgraph-review-v1')),
                            review_graph_schema_version TEXT,
                            queued_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            started_at TIMESTAMPTZ,
                            finished_at TIMESTAMPTZ,
                            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                        )
                        """
                    ).format(
                        jobs=sql.Identifier(self.jobs_table),
                        sessions=sql.Identifier(self.sessions_table),
                    )
                )
                cursor.execute(
                    sql.SQL(
                        """
                        ALTER TABLE {jobs}
                        ADD COLUMN IF NOT EXISTS heartbeat_at TIMESTAMPTZ
                        """
                    ).format(jobs=sql.Identifier(self.jobs_table))
                )
                for column_name, column_type in (
                    ("job_kind", "TEXT NOT NULL DEFAULT 'initial'"),
                    ("parent_job_id", "UUID"),
                    ("source_report_id", "UUID"),
                    ("activate_on_success", "BOOLEAN NOT NULL DEFAULT TRUE"),
                    ("idempotency_key", "TEXT"),
                    ("fencing_version", "INTEGER NOT NULL DEFAULT 0"),
                    ("report_id", "UUID"),
                    ("created_at", "TIMESTAMPTZ NOT NULL DEFAULT NOW()"),
                ):
                    cursor.execute(
                        sql.SQL(
                            "ALTER TABLE {jobs} ADD COLUMN IF NOT EXISTS {column} {kind}"
                        ).format(
                            jobs=sql.Identifier(self.jobs_table),
                            column=sql.Identifier(column_name),
                            kind=sql.SQL(column_type),
                        )
                    )
                cursor.execute(
                    sql.SQL(
                        "UPDATE {jobs} SET idempotency_key='legacy:' || job_id::text "
                        "WHERE idempotency_key IS NULL"
                    ).format(jobs=sql.Identifier(self.jobs_table))
                )
                cursor.execute(
                    sql.SQL(
                        """
                        CREATE INDEX IF NOT EXISTS {status_index}
                        ON {jobs} (status, queued_at)
                        """
                    ).format(
                        status_index=sql.Identifier(
                            runtime_schema_identifier(
                                self.table_prefix, "report_jobs_status_idx"
                            )
                        ),
                        jobs=sql.Identifier(self.jobs_table),
                    )
                )
                cursor.execute(
                    sql.SQL(
                        """
                        ALTER TABLE {jobs}
                        ADD COLUMN IF NOT EXISTS available_at TIMESTAMPTZ
                            NOT NULL DEFAULT NOW()
                        """
                    ).format(jobs=sql.Identifier(self.jobs_table))
                )
                cursor.execute(
                    sql.SQL(
                        """
                        ALTER TABLE {jobs}
                        ADD COLUMN IF NOT EXISTS scheduled_attempt INTEGER
                        """
                    ).format(jobs=sql.Identifier(self.jobs_table))
                )
                cursor.execute(
                    sql.SQL(
                        """
                        ALTER TABLE {jobs}
                        ADD COLUMN IF NOT EXISTS lease_token UUID
                        """
                    ).format(jobs=sql.Identifier(self.jobs_table))
                )
                cursor.execute(
                    sql.SQL(
                        """
                        CREATE INDEX IF NOT EXISTS {available_index}
                        ON {jobs} (status, available_at, queued_at)
                        """
                    ).format(
                        available_index=sql.Identifier(
                            runtime_schema_identifier(
                                self.table_prefix, "report_jobs_available_idx"
                            )
                        ),
                        jobs=sql.Identifier(self.jobs_table),
                    )
                )
                cursor.execute(
                    sql.SQL(
                        """
                        ALTER TABLE {jobs}
                        ADD COLUMN IF NOT EXISTS review_engine TEXT NOT NULL DEFAULT 'legacy'
                        """
                    ).format(jobs=sql.Identifier(self.jobs_table))
                )
                cursor.execute(
                    sql.SQL(
                        """
                        ALTER TABLE {jobs}
                        ADD COLUMN IF NOT EXISTS review_graph_schema_version TEXT
                        """
                    ).format(jobs=sql.Identifier(self.jobs_table))
                )
                cursor.execute(
                    sql.SQL(
                        """
                        ALTER TABLE {jobs}
                        ADD COLUMN IF NOT EXISTS last_error_code TEXT
                        """
                    ).format(jobs=sql.Identifier(self.jobs_table))
                )
                cursor.execute(
                    sql.SQL(
                        """
                        ALTER TABLE {jobs}
                        ADD COLUMN IF NOT EXISTS replay_count
                        INTEGER NOT NULL DEFAULT 0
                        """
                    ).format(jobs=sql.Identifier(self.jobs_table))
                )
                self._ensure_foreign_key(
                    cursor=cursor,
                    table_name=self.reports_table,
                    constraint_name=runtime_schema_identifier(
                        self.table_prefix, "reports_session_id_fkey"
                    ),
                )
                self._ensure_foreign_key(
                    cursor=cursor,
                    table_name=self.jobs_table,
                    constraint_name=runtime_schema_identifier(
                        self.table_prefix, "report_jobs_session_id_fkey"
                    ),
                )
                cursor.execute(
                    sql.SQL(
                        """
                        CREATE INDEX IF NOT EXISTS {lease_index}
                        ON {jobs} (status, lease_expires_at)
                        """
                    ).format(
                        lease_index=sql.Identifier(
                            runtime_schema_identifier(
                                self.table_prefix, "report_jobs_lease_idx"
                            )
                        ),
                        jobs=sql.Identifier(self.jobs_table),
                    )
                )

    def _count_rows(self, table_name: str) -> int:
        _, sql = self._import_psycopg2()
        row = self._fetchone(
            sql.SQL("SELECT COUNT(*) FROM {table}").format(table=sql.Identifier(table_name))
        )
        assert row is not None
        return row[0]

    def _fetchone(self, statement, params=None):
        psycopg2, _ = self._import_psycopg2()
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(statement, params)
                return cursor.fetchone()

    @staticmethod
    def _processing_progress_payload() -> dict:
        return {
            "stage": "retrieving",
            "percent": 20,
            "message": "Retrieving role-specific knowledge references.",
            "current_question_id": None,
        }

    def _ensure_foreign_key(
        self,
        *,
        cursor,
        table_name: str,
        constraint_name: str,
    ) -> None:
        _, sql = self._import_psycopg2()
        cursor.execute(
            sql.SQL(
                """
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1
                        FROM pg_constraint
                        WHERE conname = {constraint_name_literal}
                    ) THEN
                        ALTER TABLE {table}
                        ADD CONSTRAINT {constraint_name}
                        FOREIGN KEY (session_id)
                        REFERENCES {sessions}(session_id)
                        ON DELETE CASCADE
                        NOT VALID;
                    END IF;
                END $$;
                """
            ).format(
                constraint_name_literal=sql.Literal(constraint_name),
                table=sql.Identifier(table_name),
                constraint_name=sql.Identifier(constraint_name),
                sessions=sql.Identifier(self.sessions_table),
            )
        )

    @staticmethod
    def _job_row_to_dict(row) -> dict | None:
        if row is None:
            return None
        return {
            "job_id": str(row[0]),
            "session_id": row[1],
            "status": row[2],
            "lease_owner": row[3],
            "lease_expires_at": row[4],
            "attempt_count": row[5],
            "max_attempts": row[6],
            "last_error": row[7],
            "last_error_code": row[8],
            "replay_count": row[9],
            "review_engine": row[10],
            "review_graph_schema_version": row[11],
            "queued_at": row[12],
            "started_at": row[13],
            "finished_at": row[14],
            "updated_at": row[15],
            "lease_token": (
                str(row[16])
                if len(row) > 16 and row[16] is not None
                else None
            ),
            "available_at": row[17] if len(row) > 17 else None,
            "scheduled_attempt": row[18] if len(row) > 18 else None,
            "heartbeat_at": row[19] if len(row) > 19 else None,
        }

    @staticmethod
    def _import_psycopg2():
        try:
            import psycopg2
            from psycopg2 import sql
        except ImportError as exc:
            raise RuntimeError("psycopg2-binary is required") from exc
        return psycopg2, sql
