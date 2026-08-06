from __future__ import annotations

import json
import hashlib
from datetime import datetime, timezone
from typing import Callable
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
from app.services.report_artifact import (
    PublishReportArtifact,
    ReportArtifact,
    ReportHead,
    ReportJobV2,
    report_artifact_sha256,
)
from app.services.report_artifact_store import (
    ReportArtifactConflict,
    ReportArtifactNotFound,
)


class PostgresReportArtifactStore:
    def __init__(
        self,
        *,
        dsn: str | None = None,
        connection_provider: ConnectionProvider | None = None,
        table_prefix: str = "interview",
        schema_mode: str | None = None,
        failure_injector: Callable[[str], None] | None = None,
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
        self.sessions_table = f"{table_prefix}_sessions"
        self.jobs_table = f"{table_prefix}_report_jobs"
        self.artifacts_table = f"{table_prefix}_report_artifacts"
        self.heads_table = f"{table_prefix}_report_heads"
        self.review_runs_table = f"{table_prefix}_review_runs"
        self.failure_injector = failure_injector or (lambda _step: None)
        self.schema_mode = resolve_schema_mode(
            schema_mode,
            provider_is_owned=provider_is_owned,
        )
        if self.schema_mode == "migrate":
            self._ensure_schema()
        else:
            validate_relations(
                self._connection_provider,
                (self.jobs_table, self.artifacts_table, self.heads_table),
            )

    def enqueue_job(
        self,
        *,
        session_id: str,
        job_kind: str = "initial",
        source_report_id: str | None = None,
        parent_job_id: str | None = None,
        activate_on_success: bool = True,
        idempotency_key: str | None = None,
    ) -> ReportJobV2:
        _, sql = self._import_psycopg2()
        key = idempotency_key or f"{job_kind}:{uuid4()}"
        job_id = str(uuid4())
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL("SELECT session_id FROM {sessions} WHERE session_id=%s FOR UPDATE").format(
                        sessions=sql.Identifier(self.sessions_table)
                    ),
                    (session_id,),
                )
                if cursor.fetchone() is None:
                    raise ReportArtifactNotFound("session not found")
                cursor.execute(
                    sql.SQL(
                        "SELECT {fields} FROM {jobs} WHERE session_id=%s AND idempotency_key=%s"
                    ).format(
                        fields=self._job_fields(sql),
                        jobs=sql.Identifier(self.jobs_table),
                    ),
                    (session_id, key),
                )
                existing = cursor.fetchone()
                if existing is not None:
                    return self._job_from_row(existing)
                if source_report_id is not None:
                    cursor.execute(
                        sql.SQL("SELECT session_id FROM {artifacts} WHERE report_id=%s::uuid").format(
                            artifacts=sql.Identifier(self.artifacts_table)
                        ),
                        (source_report_id,),
                    )
                    source = cursor.fetchone()
                    if source is None or source[0] != session_id:
                        raise ReportArtifactConflict("source report does not belong to session")
                if parent_job_id is not None:
                    cursor.execute(
                        sql.SQL("SELECT session_id FROM {jobs} WHERE job_id=%s::uuid").format(
                            jobs=sql.Identifier(self.jobs_table)
                        ),
                        (parent_job_id,),
                    )
                    parent = cursor.fetchone()
                    if parent is None or parent[0] != session_id:
                        raise ReportArtifactConflict("parent job does not belong to session")
                cursor.execute(
                    sql.SQL(
                        "INSERT INTO {heads}(session_id,updated_at) VALUES(%s,NOW()) "
                        "ON CONFLICT(session_id) DO NOTHING"
                    ).format(heads=sql.Identifier(self.heads_table)),
                    (session_id,),
                )
                try:
                    cursor.execute(
                        sql.SQL(
                            "INSERT INTO {jobs}(job_id,session_id,status,job_kind,parent_job_id,"
                            "source_report_id,activate_on_success,idempotency_key,review_engine) "
                            "VALUES(%s::uuid,%s,'queued',%s,%s::uuid,%s::uuid,%s,%s,'legacy') "
                            "RETURNING {fields}"
                        ).format(
                            jobs=sql.Identifier(self.jobs_table),
                            fields=self._job_fields(sql),
                        ),
                        (
                            job_id,
                            session_id,
                            job_kind,
                            parent_job_id,
                            source_report_id,
                            activate_on_success,
                            key,
                        ),
                    )
                except Exception as exc:
                    if getattr(exc, "pgcode", None) == "23505":
                        raise ReportArtifactConflict(
                            "session already has an active report job"
                        ) from exc
                    raise
                job = self._job_from_row(cursor.fetchone())
                cursor.execute(
                    sql.SQL("UPDATE {heads} SET latest_job_id=%s::uuid,updated_at=NOW() WHERE session_id=%s").format(
                        heads=sql.Identifier(self.heads_table)
                    ),
                    (job.job_id, session_id),
                )
                return job

    def claim_job(self, job_id: str, *, worker_id: str) -> ReportJobV2:
        _, sql = self._import_psycopg2()
        lease_token = str(uuid4())
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        "UPDATE {jobs} SET status='running',lease_owner=%s,lease_token=%s::uuid,"
                        "fencing_version=fencing_version+1,lease_expires_at=NOW()+INTERVAL '5 minutes',"
                        "updated_at=NOW() WHERE job_id=%s::uuid AND status IN('queued','retrying') "
                        "RETURNING {fields}"
                    ).format(jobs=sql.Identifier(self.jobs_table), fields=self._job_fields(sql)),
                    (worker_id, lease_token, job_id),
                )
                row = cursor.fetchone()
                if row is None:
                    raise ReportArtifactConflict("job is not claimable")
                return self._job_from_row(row)

    def requeue_failed(self, job_id: str) -> ReportJobV2:
        return self._terminal_job_update(job_id, from_status="failed", to_status="queued", error_code=None)

    def fail_job(self, job_id: str, *, error_code: str) -> ReportJobV2:
        _, sql = self._import_psycopg2()
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        "UPDATE {jobs} SET status='failed',last_error_code=%s,lease_owner=NULL,"
                        "lease_token=NULL,lease_expires_at=NULL,finished_at=NOW(),updated_at=NOW() "
                        "WHERE job_id=%s::uuid AND status<>'completed' RETURNING {fields}"
                    ).format(jobs=sql.Identifier(self.jobs_table), fields=self._job_fields(sql)),
                    (error_code, job_id),
                )
                row = cursor.fetchone()
                if row is None:
                    raise ReportArtifactConflict("completed or missing job cannot fail")
                return self._job_from_row(row)

    def publish(
        self,
        job_id: str,
        payload: PublishReportArtifact,
        *,
        worker_id: str,
    ) -> ReportArtifact:
        _, sql = self._import_psycopg2()
        digest = report_artifact_sha256(payload.payload)
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL("SELECT {fields} FROM {jobs} WHERE job_id=%s::uuid FOR UPDATE").format(
                        fields=self._job_fields(sql), jobs=sql.Identifier(self.jobs_table)
                    ),
                    (job_id,),
                )
                row = cursor.fetchone()
                if row is None:
                    raise ReportArtifactNotFound("report job not found")
                job = self._job_from_row(row)
                cursor.execute(
                    sql.SQL("SELECT review_engine FROM {jobs} WHERE job_id=%s::uuid").format(
                        jobs=sql.Identifier(self.jobs_table)
                    ),
                    (job_id,),
                )
                review_engine = cursor.fetchone()[0]
                cursor.execute(
                    sql.SQL("SELECT {fields} FROM {artifacts} WHERE source_job_id=%s::uuid").format(
                        fields=self._artifact_fields(sql),
                        artifacts=sql.Identifier(self.artifacts_table),
                    ),
                    (job_id,),
                )
                existing = cursor.fetchone()
                if existing is not None:
                    artifact = self._artifact_from_row(existing)
                    if artifact.artifact_sha256 != digest:
                        raise ReportArtifactConflict("replayed job payload conflicts")
                    return artifact
                if job.status != "running" or job.lease_owner != worker_id or job.lease_token is None:
                    raise ReportArtifactConflict("job fencing token is not active")
                cursor.execute(
                    sql.SQL("SELECT state_version FROM {sessions} WHERE session_id=%s FOR UPDATE").format(
                        sessions=sql.Identifier(self.sessions_table)
                    ),
                    (job.session_id,),
                )
                session = cursor.fetchone()
                if session is None:
                    raise ReportArtifactNotFound("session not found")
                cursor.execute(
                    sql.SQL(
                        "SELECT active_report_id,latest_job_id FROM {heads} "
                        "WHERE session_id=%s FOR UPDATE"
                    ).format(heads=sql.Identifier(self.heads_table)),
                    (job.session_id,),
                )
                head = cursor.fetchone()
                if head is None:
                    raise ReportArtifactConflict("report head is missing")
                active_report_id = str(head[0]) if head[0] is not None else None
                if job.source_report_id is not None and active_report_id != job.source_report_id:
                    raise ReportArtifactConflict("rescore source is not the active report")
                cursor.execute(
                    sql.SQL("SELECT COALESCE(MAX(revision),0)+1 FROM {artifacts} WHERE session_id=%s").format(
                        artifacts=sql.Identifier(self.artifacts_table)
                    ),
                    (job.session_id,),
                )
                revision = int(cursor.fetchone()[0])
                report_id = str(uuid4())
                self.failure_injector("before_artifact")
                cursor.execute(
                    sql.SQL(
                        "INSERT INTO {artifacts}(report_id,session_id,revision,schema_version,"
                        "scoring_rubric_version,generation_status,generation_reason_code,score_status,"
                        "score_reason_code,coverage_status,report_path,payload_json,artifact_sha256,"
                        "source_report_id,supersedes_report_id,source_job_id) VALUES(" +
                        "%s::uuid,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s::uuid,%s::uuid,%s::uuid) " +
                        "RETURNING {fields}"
                    ).format(
                        artifacts=sql.Identifier(self.artifacts_table),
                        fields=self._artifact_fields(sql),
                    ),
                    (
                        report_id,
                        job.session_id,
                        revision,
                        payload.schema_version,
                        payload.scoring_rubric_version,
                        payload.generation_status,
                        payload.generation_reason_code,
                        payload.score_status,
                        payload.score_reason_code,
                        payload.coverage_status,
                        payload.report_path,
                        json.dumps(payload.payload, ensure_ascii=False),
                        digest,
                        job.source_report_id,
                        active_report_id if job.activate_on_success else None,
                        job.job_id,
                    ),
                )
                artifact = self._artifact_from_row(cursor.fetchone())
                self.failure_injector("artifact")
                cursor.execute(
                    sql.SQL(
                        "UPDATE {heads} SET active_report_id=CASE WHEN %s THEN %s::uuid ELSE active_report_id END,"
                        "latest_job_id=%s::uuid,updated_at=NOW() WHERE session_id=%s"
                    ).format(heads=sql.Identifier(self.heads_table)),
                    (job.activate_on_success, artifact.report_id, job.job_id, job.session_id),
                )
                self.failure_injector("head")
                cursor.execute(
                    sql.SQL(
                        "UPDATE {jobs} SET status='completed',report_id=%s::uuid,lease_owner=NULL,"
                        "lease_token=NULL,lease_expires_at=NULL,finished_at=NOW(),updated_at=NOW() "
                        "WHERE job_id=%s::uuid AND fencing_version=%s"
                    ).format(jobs=sql.Identifier(self.jobs_table)),
                    (artifact.report_id, job.job_id, job.fencing_version),
                )
                if cursor.rowcount != 1:
                    raise ReportArtifactConflict("job fencing changed during publish")
                self.failure_injector("job")
                cursor.execute("SELECT to_regclass(%s)", (f"public.{self.review_runs_table}",))
                review_runs_exist = cursor.fetchone()[0] is not None
                review_run = None
                if review_runs_exist:
                    cursor.execute(
                        sql.SQL(
                            "SELECT session_id,status,result_sha256 FROM {runs} "
                            "WHERE job_id=%s::uuid FOR UPDATE"
                        ).format(runs=sql.Identifier(self.review_runs_table)),
                        (job.job_id,),
                    )
                    review_run = cursor.fetchone()
                if review_run is not None:
                    if review_run[0] != job.session_id:
                        raise ReportArtifactConflict("review run belongs to another session")
                    if review_run[1] == "completed" and review_run[2] not in {None, digest}:
                        raise ReportArtifactConflict("completed review run payload conflicts")
                    cursor.execute(
                        sql.SQL(
                            "UPDATE {runs} SET status='completed',result_sha256=%s,error_code=NULL,"
                            "completed_at=COALESCE(completed_at,NOW()),updated_at=NOW() "
                            "WHERE job_id=%s::uuid"
                        ).format(runs=sql.Identifier(self.review_runs_table)),
                        (digest, job.job_id),
                    )
                elif review_engine == "langgraph-review-v1":
                    raise ReportArtifactConflict(
                        "langgraph report job is missing its durable review run"
                    )
                self.failure_injector("review_run")
                next_version = int(session[0]) + 1
                cursor.execute(
                    sql.SQL(
                        "UPDATE {sessions} SET phase='review',phase_status='completed',review_status='completed',"
                        "state_version=%s,checkpoint_version=%s,updated_at=NOW() WHERE session_id=%s"
                    ).format(sessions=sql.Identifier(self.sessions_table)),
                    (next_version, next_version, job.session_id),
                )
                self.failure_injector("session")
                return artifact

    def get_artifact(self, report_id: str) -> ReportArtifact:
        _, sql = self._import_psycopg2()
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL("SELECT {fields} FROM {artifacts} WHERE report_id=%s::uuid").format(
                        fields=self._artifact_fields(sql), artifacts=sql.Identifier(self.artifacts_table)
                    ),
                    (report_id,),
                )
                row = cursor.fetchone()
                if row is None:
                    raise ReportArtifactNotFound("report artifact not found")
                return self._artifact_from_row(row)

    def list_artifacts(self, session_id: str) -> list[ReportArtifact]:
        _, sql = self._import_psycopg2()
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL("SELECT {fields} FROM {artifacts} WHERE session_id=%s ORDER BY revision").format(
                        fields=self._artifact_fields(sql), artifacts=sql.Identifier(self.artifacts_table)
                    ),
                    (session_id,),
                )
                return [self._artifact_from_row(row) for row in cursor.fetchall()]

    def get_head(self, session_id: str) -> ReportHead:
        _, sql = self._import_psycopg2()
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL("SELECT session_id,active_report_id,latest_job_id,updated_at FROM {heads} WHERE session_id=%s").format(
                        heads=sql.Identifier(self.heads_table)
                    ),
                    (session_id,),
                )
                row = cursor.fetchone()
                if row is None:
                    raise ReportArtifactNotFound("report head not found")
                return ReportHead(
                    session_id=row[0],
                    active_report_id=str(row[1]) if row[1] is not None else None,
                    latest_job_id=str(row[2]) if row[2] is not None else None,
                    updated_at=row[3],
                )

    def list_jobs(self, session_id: str) -> list[ReportJobV2]:
        _, sql = self._import_psycopg2()
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL("SELECT {fields} FROM {jobs} WHERE session_id=%s ORDER BY created_at,job_id").format(
                        fields=self._job_fields(sql), jobs=sql.Identifier(self.jobs_table)
                    ),
                    (session_id,),
                )
                return [self._job_from_row(row) for row in cursor.fetchall()]

    def migrate_legacy_reports(self, *, session_id: str | None = None) -> int:
        """Idempotently promote completed legacy reports to revision-one artifacts."""
        _, sql = self._import_psycopg2()
        migrated = 0
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        "SELECT session_id,report_json FROM {reports} "
                        "WHERE status='completed' AND report_json IS NOT NULL "
                        + ("AND session_id=%s " if session_id else "")
                        + "ORDER BY session_id"
                    ).format(reports=sql.Identifier(f"{self.table_prefix}_reports")),
                    (session_id,) if session_id else (),
                )
                rows = cursor.fetchall()
                for legacy_session_id, report_json in rows:
                    cursor.execute(
                        sql.SQL("SELECT 1 FROM {artifacts} WHERE session_id=%s LIMIT 1").format(
                            artifacts=sql.Identifier(self.artifacts_table)
                        ),
                        (legacy_session_id,),
                    )
                    if cursor.fetchone() is not None:
                        continue
                    cursor.execute(
                        sql.SQL(
                            "SELECT job_id FROM {jobs} WHERE session_id=%s "
                            "ORDER BY created_at,job_id LIMIT 1"
                        ).format(jobs=sql.Identifier(self.jobs_table)),
                        (legacy_session_id,),
                    )
                    job_row = cursor.fetchone()
                    if job_row is None:
                        legacy_job_id = str(uuid4())
                        cursor.execute(
                            sql.SQL(
                                "INSERT INTO {jobs}(job_id,session_id,status,job_kind,"
                                "activate_on_success,idempotency_key,review_engine) "
                                "VALUES(%s::uuid,%s,'completed','initial',TRUE,%s,'legacy')"
                            ).format(jobs=sql.Identifier(self.jobs_table)),
                            (legacy_job_id, legacy_session_id, f"legacy-migration:{legacy_session_id}"),
                        )
                    else:
                        legacy_job_id = str(job_row[0])
                    payload = dict(report_json)
                    scored = isinstance(payload.get("overall_score"), int) and isinstance(
                        payload.get("overall_dimension_scores"), dict
                    )
                    report_id = str(uuid4())
                    artifact_digest = report_artifact_sha256(payload)
                    raw_digest = hashlib.sha256(
                        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
                    ).hexdigest()
                    cursor.execute(
                        sql.SQL(
                            "INSERT INTO {artifacts}(report_id,session_id,revision,schema_version,"
                            "scoring_rubric_version,generation_status,generation_reason_code,score_status,"
                            "score_reason_code,coverage_status,report_path,payload_json,artifact_sha256,"
                            "source_job_id,legacy_source_sha256,migration_version,migrated_at) VALUES("
                            "%s::uuid,%s,1,'legacy-v1','legacy-v1','complete','normal',%s,%s,%s,'legacy',"
                            "%s::jsonb,%s,%s::uuid,%s,'report-artifact-v2',NOW())"
                        ).format(artifacts=sql.Identifier(self.artifacts_table)),
                        (
                            report_id,
                            legacy_session_id,
                            "scored" if scored else "unscored",
                            "sufficient_evidence" if scored else "legacy_unknown",
                            "complete" if scored else "none",
                            json.dumps(payload, ensure_ascii=False),
                            artifact_digest,
                            legacy_job_id,
                            raw_digest,
                        ),
                    )
                    cursor.execute(
                        sql.SQL(
                            "INSERT INTO {heads}(session_id,active_report_id,latest_job_id,updated_at) "
                            "VALUES(%s,%s::uuid,%s::uuid,NOW()) ON CONFLICT(session_id) DO UPDATE "
                            "SET active_report_id=EXCLUDED.active_report_id,latest_job_id=EXCLUDED.latest_job_id,updated_at=NOW()"
                        ).format(heads=sql.Identifier(self.heads_table)),
                        (legacy_session_id, report_id, legacy_job_id),
                    )
                    cursor.execute(
                        sql.SQL("UPDATE {jobs} SET report_id=%s::uuid,status='completed',finished_at=COALESCE(finished_at,NOW()) WHERE job_id=%s::uuid").format(
                            jobs=sql.Identifier(self.jobs_table)
                        ),
                        (report_id, legacy_job_id),
                    )
                    migrated += 1
        return migrated

    def _terminal_job_update(self, job_id: str, *, from_status: str, to_status: str, error_code: str | None) -> ReportJobV2:
        _, sql = self._import_psycopg2()
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        "UPDATE {jobs} SET status=%s,last_error_code=%s,finished_at=NULL,updated_at=NOW() "
                        "WHERE job_id=%s::uuid AND status=%s RETURNING {fields}"
                    ).format(jobs=sql.Identifier(self.jobs_table), fields=self._job_fields(sql)),
                    (to_status, error_code, job_id, from_status),
                )
                row = cursor.fetchone()
                if row is None:
                    raise ReportArtifactConflict("report job status transition is invalid")
                return self._job_from_row(row)

    def _ensure_schema(self) -> None:
        _, sql = self._import_psycopg2()
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                # The artifact store is also usable as an isolated migration
                # boundary (for example in a fresh tenant/test prefix).  Do
                # not assume the legacy report-job store has already created
                # its table: create the compatible V2 base shape first, then
                # add/upgrade columns below for installations that predate
                # report artifacts.
                cursor.execute(
                    sql.SQL(
                        "CREATE TABLE IF NOT EXISTS {jobs}("
                        "job_id UUID PRIMARY KEY,"
                        "session_id TEXT NOT NULL REFERENCES {sessions}(session_id) ON DELETE CASCADE,"
                        "status TEXT NOT NULL CHECK(status IN('queued','running','retrying','completed','failed')),"
                        "lease_owner TEXT,lease_token UUID,lease_expires_at TIMESTAMPTZ,"
                        "heartbeat_at TIMESTAMPTZ,available_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),"
                        "scheduled_attempt INTEGER,attempt_count INTEGER NOT NULL DEFAULT 0,"
                        "max_attempts INTEGER NOT NULL DEFAULT 3,last_error TEXT,last_error_code TEXT,"
                        "replay_count INTEGER NOT NULL DEFAULT 0,review_engine TEXT NOT NULL DEFAULT 'legacy',"
                        "review_graph_schema_version TEXT,queued_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),"
                        "started_at TIMESTAMPTZ,finished_at TIMESTAMPTZ,updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()"
                        ")"
                    ).format(
                        jobs=sql.Identifier(self.jobs_table),
                        sessions=sql.Identifier(self.sessions_table),
                    )
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
                        sql.SQL("ALTER TABLE {jobs} ADD COLUMN IF NOT EXISTS {column} {kind}").format(
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
                        "ALTER TABLE {jobs} ALTER COLUMN idempotency_key SET NOT NULL"
                    ).format(jobs=sql.Identifier(self.jobs_table))
                )
                cursor.execute(
                    "SELECT conname FROM pg_constraint WHERE conrelid=to_regclass(%s) "
                    "AND contype='u' AND pg_get_constraintdef(oid)='UNIQUE (session_id)'",
                    (f"public.{self.jobs_table}",),
                )
                for (constraint_name,) in cursor.fetchall():
                    cursor.execute(
                        sql.SQL("ALTER TABLE {jobs} DROP CONSTRAINT {constraint}").format(
                            jobs=sql.Identifier(self.jobs_table),
                            constraint=sql.Identifier(constraint_name),
                        )
                    )
                cursor.execute(
                    sql.SQL(
                        "CREATE TABLE IF NOT EXISTS {artifacts}("
                        "report_id UUID PRIMARY KEY,session_id TEXT NOT NULL REFERENCES {sessions}(session_id) ON DELETE CASCADE,"
                        "revision INTEGER NOT NULL CHECK(revision>=1),schema_version TEXT NOT NULL,"
                        "scoring_rubric_version TEXT NOT NULL,generation_status TEXT NOT NULL,"
                        "generation_reason_code TEXT NOT NULL,score_status TEXT NOT NULL,score_reason_code TEXT NOT NULL,"
                        "coverage_status TEXT NOT NULL,report_path TEXT NOT NULL,payload_json JSONB NOT NULL,"
                        "artifact_sha256 TEXT NOT NULL,source_report_id UUID REFERENCES {artifacts}(report_id),"
                        "supersedes_report_id UUID REFERENCES {artifacts}(report_id),source_job_id UUID NOT NULL UNIQUE REFERENCES {jobs}(job_id),"
                        "created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),UNIQUE(session_id,revision))"
                    ).format(
                        artifacts=sql.Identifier(self.artifacts_table),
                        sessions=sql.Identifier(self.sessions_table),
                        jobs=sql.Identifier(self.jobs_table),
                    )
                )
                for column_name, column_type in (
                    ("legacy_source_sha256", "TEXT"),
                    ("migration_version", "TEXT"),
                    ("migrated_at", "TIMESTAMPTZ"),
                ):
                    cursor.execute(
                        sql.SQL(
                            "ALTER TABLE {artifacts} ADD COLUMN IF NOT EXISTS {column} {kind}"
                        ).format(
                            artifacts=sql.Identifier(self.artifacts_table),
                            column=sql.Identifier(column_name),
                            kind=sql.SQL(column_type),
                        )
                    )
                cursor.execute(
                    sql.SQL(
                        "CREATE TABLE IF NOT EXISTS {heads}(session_id TEXT PRIMARY KEY REFERENCES {sessions}(session_id) ON DELETE CASCADE,"
                        "active_report_id UUID REFERENCES {artifacts}(report_id),latest_job_id UUID REFERENCES {jobs}(job_id),"
                        "updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())"
                    ).format(
                        heads=sql.Identifier(self.heads_table),
                        sessions=sql.Identifier(self.sessions_table),
                        artifacts=sql.Identifier(self.artifacts_table),
                        jobs=sql.Identifier(self.jobs_table),
                    )
                )
                cursor.execute(
                    sql.SQL(
                        "INSERT INTO {heads}(session_id,latest_job_id,updated_at) "
                        "SELECT DISTINCT ON (session_id) session_id,job_id,NOW() FROM {jobs} "
                        "ORDER BY session_id,created_at DESC,job_id DESC "
                        "ON CONFLICT(session_id) DO UPDATE SET latest_job_id="
                        "COALESCE({heads}.latest_job_id,EXCLUDED.latest_job_id),updated_at=NOW()"
                    ).format(
                        heads=sql.Identifier(self.heads_table),
                        jobs=sql.Identifier(self.jobs_table),
                    )
                )
                cursor.execute(
                    sql.SQL("CREATE UNIQUE INDEX IF NOT EXISTS {index} ON {jobs}(session_id) WHERE status IN('queued','running','retrying')").format(
                        index=sql.Identifier(runtime_schema_identifier(self.table_prefix, "report_jobs_active_session_uq")),
                        jobs=sql.Identifier(self.jobs_table),
                    )
                )
                cursor.execute(
                    sql.SQL("CREATE UNIQUE INDEX IF NOT EXISTS {index} ON {jobs}(session_id,idempotency_key) WHERE idempotency_key IS NOT NULL").format(
                        index=sql.Identifier(runtime_schema_identifier(self.table_prefix, "report_jobs_idempotency_uq")),
                        jobs=sql.Identifier(self.jobs_table),
                    )
                )
                cursor.execute(
                    sql.SQL("CREATE INDEX IF NOT EXISTS {index} ON {jobs}(session_id,created_at)").format(
                        index=sql.Identifier(runtime_schema_identifier(self.table_prefix, "report_jobs_session_history_idx")),
                        jobs=sql.Identifier(self.jobs_table),
                    )
                )
                function_name = runtime_schema_identifier(self.table_prefix, "report_artifacts_immutable_fn")
                trigger_name = runtime_schema_identifier(self.table_prefix, "report_artifacts_immutable_trg")
                cursor.execute(
                    sql.SQL(
                        "CREATE OR REPLACE FUNCTION {function}() RETURNS trigger LANGUAGE plpgsql AS $$ "
                        "BEGIN RAISE EXCEPTION 'report artifacts are immutable'; END; $$"
                    ).format(function=sql.Identifier(function_name))
                )
                cursor.execute(
                    "SELECT 1 FROM pg_trigger WHERE tgname=%s AND tgrelid=to_regclass(%s)",
                    (trigger_name, f"public.{self.artifacts_table}"),
                )
                if cursor.fetchone() is None:
                    cursor.execute(
                        sql.SQL("CREATE TRIGGER {trigger} BEFORE UPDATE OR DELETE ON {artifacts} FOR EACH ROW EXECUTE FUNCTION {function}()").format(
                            trigger=sql.Identifier(trigger_name),
                            artifacts=sql.Identifier(self.artifacts_table),
                            function=sql.Identifier(function_name),
                        )
                    )

                reference_function = runtime_schema_identifier(
                    self.table_prefix, "report_artifact_references_fn"
                )
                reference_trigger = runtime_schema_identifier(
                    self.table_prefix, "report_artifact_references_trg"
                )
                cursor.execute(
                    sql.SQL(
                        "CREATE OR REPLACE FUNCTION {function}() RETURNS trigger LANGUAGE plpgsql AS $$ "
                        "DECLARE referenced_session TEXT; BEGIN "
                        "IF NEW.source_report_id IS NOT NULL THEN "
                        "SELECT session_id INTO referenced_session FROM {artifacts} "
                        "WHERE report_id=NEW.source_report_id; "
                        "IF referenced_session IS DISTINCT FROM NEW.session_id THEN "
                        "RAISE EXCEPTION 'source report must belong to the artifact session'; END IF; "
                        "END IF; "
                        "IF NEW.supersedes_report_id IS NOT NULL THEN "
                        "SELECT session_id INTO referenced_session FROM {artifacts} "
                        "WHERE report_id=NEW.supersedes_report_id; "
                        "IF referenced_session IS DISTINCT FROM NEW.session_id THEN "
                        "RAISE EXCEPTION 'superseded report must belong to the artifact session'; END IF; "
                        "END IF; RETURN NEW; END; $$"
                    ).format(
                        function=sql.Identifier(reference_function),
                        artifacts=sql.Identifier(self.artifacts_table),
                    )
                )
                cursor.execute(
                    "SELECT 1 FROM pg_trigger WHERE tgname=%s AND tgrelid=to_regclass(%s)",
                    (reference_trigger, f"public.{self.artifacts_table}"),
                )
                if cursor.fetchone() is None:
                    cursor.execute(
                        sql.SQL(
                            "CREATE TRIGGER {trigger} BEFORE INSERT ON {artifacts} "
                            "FOR EACH ROW EXECUTE FUNCTION {function}()"
                        ).format(
                            trigger=sql.Identifier(reference_trigger),
                            artifacts=sql.Identifier(self.artifacts_table),
                            function=sql.Identifier(reference_function),
                        )
                    )

                head_function = runtime_schema_identifier(
                    self.table_prefix, "report_head_references_fn"
                )
                head_trigger = runtime_schema_identifier(
                    self.table_prefix, "report_head_references_trg"
                )
                cursor.execute(
                    sql.SQL(
                        "CREATE OR REPLACE FUNCTION {function}() RETURNS trigger LANGUAGE plpgsql AS $$ "
                        "DECLARE referenced_session TEXT; BEGIN "
                        "IF NEW.active_report_id IS NOT NULL THEN "
                        "SELECT session_id INTO referenced_session FROM {artifacts} "
                        "WHERE report_id=NEW.active_report_id; "
                        "IF referenced_session IS DISTINCT FROM NEW.session_id THEN "
                        "RAISE EXCEPTION 'active report must belong to the head session'; END IF; "
                        "END IF; "
                        "IF NEW.latest_job_id IS NOT NULL THEN "
                        "SELECT session_id INTO referenced_session FROM {jobs} "
                        "WHERE job_id=NEW.latest_job_id; "
                        "IF referenced_session IS DISTINCT FROM NEW.session_id THEN "
                        "RAISE EXCEPTION 'latest job must belong to the head session'; END IF; "
                        "END IF; RETURN NEW; END; $$"
                    ).format(
                        function=sql.Identifier(head_function),
                        artifacts=sql.Identifier(self.artifacts_table),
                        jobs=sql.Identifier(self.jobs_table),
                    )
                )
                cursor.execute(
                    "SELECT 1 FROM pg_trigger WHERE tgname=%s AND tgrelid=to_regclass(%s)",
                    (head_trigger, f"public.{self.heads_table}"),
                )
                if cursor.fetchone() is None:
                    cursor.execute(
                        sql.SQL(
                            "CREATE TRIGGER {trigger} BEFORE INSERT OR UPDATE ON {heads} "
                            "FOR EACH ROW EXECUTE FUNCTION {function}()"
                        ).format(
                            trigger=sql.Identifier(head_trigger),
                            heads=sql.Identifier(self.heads_table),
                            function=sql.Identifier(head_function),
                        )
                    )

    @staticmethod
    def _job_fields(sql):
        return sql.SQL(",").join(
            map(
                sql.Identifier,
                (
                    "job_id", "session_id", "job_kind", "parent_job_id", "source_report_id",
                    "activate_on_success", "status", "idempotency_key", "lease_owner", "lease_token",
                    "fencing_version", "last_error_code", "report_id", "created_at", "updated_at",
                ),
            )
        )

    @staticmethod
    def _artifact_fields(sql):
        return sql.SQL(",").join(
            map(
                sql.Identifier,
                (
                    "report_id", "session_id", "revision", "schema_version", "scoring_rubric_version",
                    "generation_status", "generation_reason_code", "score_status", "score_reason_code",
                    "coverage_status", "report_path", "payload_json", "artifact_sha256", "source_report_id",
                    "supersedes_report_id", "source_job_id", "created_at",
                ),
            )
        )

    @staticmethod
    def _job_from_row(row) -> ReportJobV2:
        return ReportJobV2(
            job_id=str(row[0]), session_id=row[1], job_kind=row[2],
            parent_job_id=str(row[3]) if row[3] else None,
            source_report_id=str(row[4]) if row[4] else None,
            activate_on_success=bool(row[5]), status=row[6], idempotency_key=row[7],
            lease_owner=row[8], lease_token=str(row[9]) if row[9] else None,
            fencing_version=int(row[10]), error_code=row[11],
            report_id=str(row[12]) if row[12] else None,
            created_at=row[13], updated_at=row[14],
        )

    @staticmethod
    def _artifact_from_row(row) -> ReportArtifact:
        return ReportArtifact(
            report_id=str(row[0]), session_id=row[1], revision=int(row[2]),
            schema_version=row[3], scoring_rubric_version=row[4], generation_status=row[5],
            generation_reason_code=row[6], score_status=row[7], score_reason_code=row[8],
            coverage_status=row[9], report_path=row[10], payload=row[11], artifact_sha256=row[12],
            source_report_id=str(row[13]) if row[13] else None,
            supersedes_report_id=str(row[14]) if row[14] else None,
            source_job_id=str(row[15]), created_at=row[16],
        )

    @staticmethod
    def _import_psycopg2():
        import psycopg2
        from psycopg2 import sql

        return psycopg2, sql
