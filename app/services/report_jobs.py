import json
from uuid import uuid4


class PostgresReportJobStore:
    def __init__(
        self,
        *,
        dsn: str,
        table_prefix: str = "interview",
        lease_seconds: int = 300,
    ) -> None:
        self.dsn = dsn
        self.table_prefix = table_prefix
        self.lease_seconds = lease_seconds
        self.sessions_table = f"{table_prefix}_sessions"
        self.reports_table = f"{table_prefix}_reports"
        self.jobs_table = f"{table_prefix}_report_jobs"
        self._ensure_schema()

    def drop_tables(self) -> None:
        psycopg2, sql = self._import_psycopg2()
        with psycopg2.connect(self.dsn) as connection:
            with connection.cursor() as cursor:
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
                       attempt_count, max_attempts, last_error, queued_at,
                       started_at, finished_at, updated_at
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
                       attempt_count, max_attempts, last_error, queued_at,
                       started_at, finished_at, updated_at
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
        progress_json = json.dumps(self._processing_progress_payload(), ensure_ascii=False)
        with psycopg2.connect(self.dsn) as connection:
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
                cursor.execute(
                    sql.SQL(
                        """
                        INSERT INTO {jobs} (
                            job_id, session_id, status, attempt_count, max_attempts
                        )
                        VALUES (%s::uuid, %s, 'queued', 0, 3)
                        ON CONFLICT (session_id) DO UPDATE
                        SET session_id = EXCLUDED.session_id
                        RETURNING job_id, session_id, status, lease_owner, lease_expires_at,
                                  attempt_count, max_attempts, last_error, queued_at,
                                  started_at, finished_at, updated_at
                        """
                    ).format(jobs=sql.Identifier(self.jobs_table)),
                    (job_id, session_id),
                )
                row = cursor.fetchone()
        return self._job_row_to_dict(row)

    def claim_next(self, worker_id: str, lease_seconds: int | None = None) -> dict | None:
        psycopg2, sql = self._import_psycopg2()
        lease_duration = self.lease_seconds if lease_seconds is None else lease_seconds
        with psycopg2.connect(self.dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        """
                        WITH next_job AS (
                            SELECT job_id
                            FROM {jobs}
                            WHERE status = 'queued'
                               OR status = 'retrying'
                               OR (status = 'running' AND lease_expires_at <= NOW())
                            ORDER BY queued_at
                            FOR UPDATE SKIP LOCKED
                            LIMIT 1
                        )
                        UPDATE {jobs} AS jobs
                        SET status = 'running',
                            lease_owner = %s,
                            lease_expires_at = NOW() + (%s * INTERVAL '1 second'),
                            started_at = COALESCE(jobs.started_at, NOW()),
                            updated_at = NOW()
                        FROM next_job
                        WHERE jobs.job_id = next_job.job_id
                        RETURNING jobs.job_id, jobs.session_id, jobs.status, jobs.lease_owner,
                                  jobs.lease_expires_at, jobs.attempt_count, jobs.max_attempts,
                                  jobs.last_error, jobs.queued_at, jobs.started_at,
                                  jobs.finished_at, jobs.updated_at
                        """
                    ).format(jobs=sql.Identifier(self.jobs_table)),
                    (worker_id, lease_duration),
                )
                row = cursor.fetchone()
        return self._job_row_to_dict(row)

    def mark_completed(self, job_id: str) -> dict | None:
        psycopg2, sql = self._import_psycopg2()
        with psycopg2.connect(self.dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        """
                        UPDATE {jobs}
                        SET status = 'completed',
                            lease_owner = NULL,
                            lease_expires_at = NULL,
                            finished_at = NOW(),
                            updated_at = NOW()
                        WHERE job_id = %s::uuid
                        RETURNING job_id, session_id, status, lease_owner, lease_expires_at,
                                  attempt_count, max_attempts, last_error, queued_at,
                                  started_at, finished_at, updated_at
                        """
                    ).format(jobs=sql.Identifier(self.jobs_table)),
                    (job_id,),
                )
                row = cursor.fetchone()
        return self._job_row_to_dict(row)

    def mark_retryable_failure(self, job_id: str, error: str) -> dict | None:
        psycopg2, sql = self._import_psycopg2()
        progress_json = json.dumps(self._processing_progress_payload(), ensure_ascii=False)
        with psycopg2.connect(self.dsn) as connection:
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
                                lease_owner = NULL,
                                lease_expires_at = NULL,
                                finished_at = CASE
                                    WHEN attempt_count + 1 >= max_attempts THEN NOW()
                                    ELSE finished_at
                                END,
                                updated_at = NOW()
                            WHERE job_id = %s::uuid
                            RETURNING job_id, session_id, status, lease_owner, lease_expires_at,
                                      attempt_count, max_attempts, last_error, queued_at,
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
                                  updated_job.last_error, updated_job.queued_at,
                                  updated_job.started_at, updated_job.finished_at,
                                  updated_job.updated_at
                        """
                    ).format(
                        jobs=sql.Identifier(self.jobs_table),
                        reports=sql.Identifier(self.reports_table),
                    ),
                    (error, job_id, progress_json, error),
                )
                row = cursor.fetchone()
        return self._job_row_to_dict(row)

    def mark_failed(self, job_id: str, error: str) -> dict | None:
        psycopg2, sql = self._import_psycopg2()
        with psycopg2.connect(self.dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        """
                        WITH updated_job AS (
                            UPDATE {jobs}
                            SET status = 'failed',
                                last_error = %s,
                                lease_owner = NULL,
                                lease_expires_at = NULL,
                                finished_at = NOW(),
                                updated_at = NOW()
                            WHERE job_id = %s::uuid
                            RETURNING job_id, session_id, status, lease_owner, lease_expires_at,
                                      attempt_count, max_attempts, last_error, queued_at,
                                      started_at, finished_at, updated_at
                        )
                        UPDATE {reports} AS reports
                        SET status = 'failed',
                            progress_json = NULL,
                            report_json = NULL,
                            error = %s,
                            updated_at = NOW()
                        FROM updated_job
                        WHERE reports.session_id = updated_job.session_id
                        RETURNING updated_job.job_id, updated_job.session_id, updated_job.status,
                                  updated_job.lease_owner, updated_job.lease_expires_at,
                                  updated_job.attempt_count, updated_job.max_attempts,
                                  updated_job.last_error, updated_job.queued_at,
                                  updated_job.started_at, updated_job.finished_at,
                                  updated_job.updated_at
                        """
                    ).format(
                        jobs=sql.Identifier(self.jobs_table),
                        reports=sql.Identifier(self.reports_table),
                    ),
                    (error, job_id, error),
                )
                row = cursor.fetchone()
        return self._job_row_to_dict(row)

    def insert_processing_report_only(self, session_id: str) -> None:
        psycopg2, sql = self._import_psycopg2()
        progress_json = json.dumps(self._processing_progress_payload(), ensure_ascii=False)
        with psycopg2.connect(self.dsn) as connection:
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
        with psycopg2.connect(self.dsn) as connection:
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
        with psycopg2.connect(self.dsn) as connection:
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
                            lease_expires_at TIMESTAMPTZ,
                            attempt_count INTEGER NOT NULL DEFAULT 0,
                            max_attempts INTEGER NOT NULL DEFAULT 3,
                            last_error TEXT,
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
                        CREATE INDEX IF NOT EXISTS {status_index}
                        ON {jobs} (status, queued_at)
                        """
                    ).format(
                        status_index=sql.Identifier(f"{self.jobs_table}_status_idx"),
                        jobs=sql.Identifier(self.jobs_table),
                    )
                )
                self._ensure_foreign_key(
                    cursor=cursor,
                    table_name=self.reports_table,
                    constraint_name=f"{self.reports_table}_session_id_fkey",
                )
                self._ensure_foreign_key(
                    cursor=cursor,
                    table_name=self.jobs_table,
                    constraint_name=f"{self.jobs_table}_session_id_fkey",
                )
                cursor.execute(
                    sql.SQL(
                        """
                        CREATE INDEX IF NOT EXISTS {lease_index}
                        ON {jobs} (status, lease_expires_at)
                        """
                    ).format(
                        lease_index=sql.Identifier(f"{self.jobs_table}_lease_idx"),
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
        with psycopg2.connect(self.dsn) as connection:
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
            "queued_at": row[8],
            "started_at": row[9],
            "finished_at": row[10],
            "updated_at": row[11],
        }

    @staticmethod
    def _import_psycopg2():
        try:
            import psycopg2
            from psycopg2 import sql
        except ImportError as exc:
            raise RuntimeError("psycopg2-binary is required") from exc
        return psycopg2, sql
