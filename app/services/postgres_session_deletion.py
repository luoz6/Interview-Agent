from __future__ import annotations

from uuid import uuid4

from app.services.postgres_connections import (
    ConnectionProvider,
    DirectPsycopg2ConnectionProvider,
)
from app.services.postgres_identifiers import (
    runtime_schema_identifier,
    validate_runtime_table_prefix,
)
from app.services.postgres_schema import resolve_schema_mode, validate_relations
from app.services.session_deletion import SessionDeletionJob


class PostgresSessionDeletionJobStore:
    def __init__(
        self,
        *,
        dsn: str | None = None,
        connection_provider: ConnectionProvider | None = None,
        table_prefix: str = "interview",
        schema_mode: str | None = None,
    ) -> None:
        validate_runtime_table_prefix(table_prefix)
        if connection_provider is None:
            if not dsn:
                raise ValueError("dsn or connection_provider is required")
            connection_provider = DirectPsycopg2ConnectionProvider(dsn)
            provider_is_owned = True
        else:
            provider_is_owned = False
        self._connection_provider = connection_provider
        self.table_prefix = table_prefix
        self.table = f"{table_prefix}_session_deletion_jobs"
        self.schema_mode = resolve_schema_mode(
            schema_mode,
            provider_is_owned=provider_is_owned,
        )
        if self.schema_mode == "migrate":
            self._ensure_schema()
        else:
            validate_relations(
                self._connection_provider,
                (self.table, f"{table_prefix}_schema_migrations"),
            )

    def request(self, session_id: str) -> SessionDeletionJob:
        from psycopg2 import extras, sql

        deletion_job_id = str(uuid4())
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        """
                        INSERT INTO {table} (
                            deletion_job_id, session_id, status, safe_counts
                        ) VALUES (%s::uuid, %s, 'queued', %s)
                        ON CONFLICT (session_id) DO NOTHING
                        """
                    ).format(table=sql.Identifier(self.table)),
                    (deletion_job_id, session_id, extras.Json({})),
                )
                cursor.execute(
                    self._select_sql(sql, "WHERE session_id = %s"),
                    (session_id,),
                )
                row = cursor.fetchone()
            connection.commit()
        return self._from_row(row)

    def get_for_session(self, session_id: str) -> SessionDeletionJob | None:
        from psycopg2 import sql

        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    self._select_sql(sql, "WHERE session_id = %s"),
                    (session_id,),
                )
                row = cursor.fetchone()
        return None if row is None else self._from_row(row)

    def claim(
        self, *, worker_id: str, lease_seconds: int
    ) -> SessionDeletionJob | None:
        if lease_seconds <= 0:
            raise ValueError("deletion lease must be positive")
        from psycopg2 import sql

        lease_token = str(uuid4())
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        """
                        WITH candidate AS (
                            SELECT deletion_job_id
                            FROM {table}
                            WHERE status IN ('queued', 'failed')
                               OR (
                                   status = 'running'
                                   AND lease_expires_at <= NOW()
                               )
                            ORDER BY created_at, deletion_job_id
                            FOR UPDATE SKIP LOCKED
                            LIMIT 1
                        )
                        UPDATE {table} AS jobs
                        SET status = 'running',
                            attempt_count = jobs.attempt_count + 1,
                            lease_owner = %s,
                            lease_token = %s::uuid,
                            lease_expires_at =
                                NOW() + (%s * INTERVAL '1 second'),
                            fencing_version = jobs.fencing_version + 1,
                            error_code = NULL,
                            updated_at = NOW()
                        FROM candidate
                        WHERE jobs.deletion_job_id = candidate.deletion_job_id
                        RETURNING jobs.deletion_job_id::text, jobs.session_id,
                                  jobs.status, jobs.attempt_count,
                                  jobs.lease_owner, jobs.lease_token::text,
                                  jobs.lease_expires_at, jobs.fencing_version,
                                  jobs.error_code, jobs.safe_counts,
                                  jobs.created_at, jobs.updated_at,
                                  jobs.completed_at
                        """
                    ).format(table=sql.Identifier(self.table)),
                    (worker_id, lease_token, lease_seconds),
                )
                row = cursor.fetchone()
            connection.commit()
        return None if row is None else self._from_row(row)

    def complete(
        self, job: SessionDeletionJob, *, safe_counts: dict[str, int]
    ) -> SessionDeletionJob:
        return self._finish_claim(
            job,
            status="completed",
            safe_counts=safe_counts,
            error_code=None,
        )

    def fail(
        self, job: SessionDeletionJob, *, error_code: str
    ) -> SessionDeletionJob:
        return self._finish_claim(
            job,
            status="failed",
            safe_counts=job.safe_counts,
            error_code=error_code,
        )

    def _finish_claim(self, job, *, status, safe_counts, error_code):
        from psycopg2 import extras, sql

        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        """
                        UPDATE {table}
                        SET status = %s,
                            safe_counts = %s,
                            error_code = %s,
                            lease_owner = NULL,
                            lease_token = NULL,
                            lease_expires_at = NULL,
                            completed_at = CASE
                                WHEN %s = 'completed' THEN NOW()
                                ELSE completed_at
                            END,
                            updated_at = NOW()
                        WHERE deletion_job_id = %s::uuid
                          AND status = 'running'
                          AND lease_owner = %s
                          AND lease_token = %s::uuid
                          AND fencing_version = %s
                        RETURNING deletion_job_id::text, session_id, status,
                                  attempt_count, lease_owner,
                                  lease_token::text, lease_expires_at,
                                  fencing_version, error_code, safe_counts,
                                  created_at, updated_at, completed_at
                        """
                    ).format(table=sql.Identifier(self.table)),
                    (
                        status,
                        extras.Json(dict(safe_counts)),
                        error_code,
                        status,
                        self._raw_job_id(job.job_id),
                        job.lease_owner,
                        job.lease_token,
                        job.fencing_version,
                    ),
                )
                row = cursor.fetchone()
                if row is None:
                    raise RuntimeError("session deletion lease was lost")
            connection.commit()
        return self._from_row(row)

    def _ensure_schema(self) -> None:
        from psycopg2 import sql

        table = sql.Identifier(self.table)
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        """
                        CREATE TABLE IF NOT EXISTS {table} (
                            deletion_job_id UUID PRIMARY KEY,
                            session_id TEXT NOT NULL UNIQUE,
                            status TEXT NOT NULL CHECK (
                                status IN ('queued','running','completed','failed')
                            ),
                            attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (
                                attempt_count >= 0
                            ),
                            lease_owner TEXT,
                            lease_token UUID,
                            lease_expires_at TIMESTAMPTZ,
                            fencing_version BIGINT NOT NULL DEFAULT 0 CHECK (
                                fencing_version >= 0
                            ),
                            error_code TEXT,
                            safe_counts JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            completed_at TIMESTAMPTZ,
                            CHECK (
                                (status = 'running'
                                 AND lease_owner IS NOT NULL
                                 AND lease_token IS NOT NULL
                                 AND lease_expires_at IS NOT NULL)
                                OR
                                (status <> 'running'
                                 AND lease_owner IS NULL
                                 AND lease_token IS NULL
                                 AND lease_expires_at IS NULL)
                            )
                        )
                        """
                    ).format(table=table)
                )
                retry_index = runtime_schema_identifier(
                    self.table_prefix,
                    "session_deletion_queued_idx",
                )
                cursor.execute(
                    sql.SQL("DROP INDEX IF EXISTS {index}").format(
                        index=sql.Identifier(retry_index)
                    )
                )
                cursor.execute(
                    sql.SQL(
                        "CREATE INDEX {index} ON {table} "
                        "(status, created_at) "
                        "WHERE status IN ('queued','failed')"
                    ).format(
                        index=sql.Identifier(retry_index),
                        table=table,
                    )
                )
                cursor.execute(
                    sql.SQL(
                        "CREATE INDEX IF NOT EXISTS {index} ON {table} "
                        "(lease_expires_at) WHERE status='running'"
                    ).format(
                        index=sql.Identifier(
                            runtime_schema_identifier(
                                self.table_prefix,
                                "session_deletion_stale_lease_idx",
                            )
                        ),
                        table=table,
                    )
                )
            connection.commit()

    def _select_sql(self, sql, suffix):
        return sql.SQL(
            "SELECT deletion_job_id::text, session_id, status, attempt_count, "
            "lease_owner, lease_token::text, lease_expires_at, fencing_version, "
            "error_code, safe_counts, created_at, updated_at, completed_at "
            "FROM {table} " + suffix
        ).format(table=sql.Identifier(self.table))

    @staticmethod
    def _from_row(row) -> SessionDeletionJob:
        return SessionDeletionJob(
            job_id=f"delete-{row[0]}",
            session_id=row[1],
            status=row[2],
            attempt_count=row[3],
            lease_owner=row[4],
            lease_token=row[5],
            lease_expires_at=row[6],
            fencing_version=row[7],
            error_code=row[8],
            safe_counts=dict(row[9] or {}),
            created_at=row[10],
            updated_at=row[11],
            completed_at=row[12],
        )

    @staticmethod
    def _raw_job_id(job_id: str) -> str:
        if not job_id.startswith("delete-"):
            raise ValueError("invalid deletion job id")
        return job_id[len("delete-") :]
