from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from app.services.postgres_runtime_control import PostgresRuntimeControlStore
from app.services.runtime_domain_events import ReviewRetryDueEvent


class ReportCommitConflict(RuntimeError):
    pass


@dataclass(frozen=True)
class ReviewRun:
    job_id: str
    session_id: str
    graph_schema_version: str
    input_sha256: str
    status: str
    result_sha256: str | None
    error_code: str | None
    provider_attempt: int
    quality_repair_count: int


class PostgresReviewWorkflowStore:
    def __init__(self, *, dsn: str, table_prefix: str = "interview") -> None:
        self.table_prefix = table_prefix
        self.sessions_table = f"{table_prefix}_sessions"
        self.reports_table = f"{table_prefix}_reports"
        self.jobs_table = f"{table_prefix}_report_jobs"
        self.runs_table = f"{table_prefix}_review_runs"
        self.artifacts_table = f"{table_prefix}_review_artifacts"
        self.control = PostgresRuntimeControlStore(dsn=dsn, table_prefix=table_prefix)
        self._ensure_schema()

    def initialize_run(self, *, job_id: str, session_id: str, graph_schema_version: str, input_sha256: str) -> ReviewRun:
        with self.control.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(self._sql("""
                    INSERT INTO {runs} (job_id, session_id, graph_schema_version, input_sha256, status)
                    VALUES (%s::uuid, %s, %s, %s, 'running')
                    ON CONFLICT (job_id) DO NOTHING
                """), (job_id, session_id, graph_schema_version, input_sha256))
                run = self._get_run(cursor, job_id, lock=True)
                if run.session_id != session_id or run.graph_schema_version != graph_schema_version or run.input_sha256 != input_sha256:
                    raise ReportCommitConflict(job_id)
                return run

    def schedule_retry(self, *, job_id: str, next_attempt_number: int, delay_seconds: float) -> str:
        event_id = f"review-{job_id}-retry-{next_attempt_number}"
        with self.control.connection() as connection:
            with connection.cursor() as cursor:
                run = self._get_run(cursor, job_id, lock=True)
                if run.status == "completed":
                    return event_id
                event = ReviewRetryDueEvent(event_id=event_id, session_id=run.session_id, report_job_id=job_id, next_attempt_number=next_attempt_number)
                cursor.execute(self._sql("""
                    UPDATE {runs} SET status = 'waiting', provider_attempt = %s, updated_at = NOW()
                    WHERE job_id = %s::uuid
                """), (next_attempt_number, job_id))
                cursor.execute(self._sql("""
                    INSERT INTO {outbox} (event_id, session_id, correlation_id, event_type, schema_version, payload_json, status, available_at)
                    VALUES (%s, %s, %s, %s, %s, %s::jsonb, 'pending', NOW() + (%s * INTERVAL '1 second'))
                    ON CONFLICT (event_id) DO NOTHING
                """), (event.event_id, event.session_id, event.correlation_id, event.event_type, event.schema_version, event.model_dump_json(), delay_seconds))
        return event_id

    def commit_report(self, *, job_id: str, report) -> int:
        report_json = report.model_dump(mode="json")
        digest = hashlib.sha256(json.dumps(report_json, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        with self.control.connection() as connection:
            with connection.cursor() as cursor:
                run = self._get_run(cursor, job_id, lock=True)
                if run.status == "completed":
                    if run.result_sha256 != digest:
                        raise ReportCommitConflict(job_id)
                    cursor.execute(self._sql("SELECT state_version FROM {sessions} WHERE session_id = %s"), (run.session_id,))
                    return int(cursor.fetchone()[0])
                cursor.execute(self._sql("SELECT state_version FROM {sessions} WHERE session_id = %s FOR UPDATE"), (run.session_id,))
                row = cursor.fetchone()
                if row is None:
                    raise ValueError("session not found")
                state_version = int(row[0]) + 1
                cursor.execute(self._sql("""
                    UPDATE {sessions} SET phase = 'review', phase_status = 'completed', review_status = 'completed',
                        state_version = %s, checkpoint_version = %s, updated_at = NOW()
                    WHERE session_id = %s
                """), (state_version, state_version, run.session_id))
                cursor.execute(self._sql("""
                    UPDATE {reports} SET status = 'completed', report_json = %s::jsonb, error = NULL,
                        completed_at = NOW(), failed_at = NULL, updated_at = NOW()
                    WHERE session_id = %s
                """), (json.dumps(report_json, ensure_ascii=False), run.session_id))
                if cursor.rowcount != 1:
                    raise ValueError("report record not found")
                cursor.execute(self._sql("""
                    UPDATE {jobs} SET status = 'completed', lease_owner = NULL, lease_expires_at = NULL,
                        finished_at = NOW(), updated_at = NOW() WHERE job_id = %s::uuid
                """), (job_id,))
                cursor.execute(self._sql("""
                    UPDATE {runs} SET status = 'completed', result_sha256 = %s, error_code = NULL,
                        completed_at = NOW(), updated_at = NOW() WHERE job_id = %s::uuid
                """), (digest, job_id))
                return state_version

    def save_report_artifact(self, *, job_id: str, report) -> dict:
        payload = report.model_dump(mode="json")
        digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        with self.control.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(self._sql("""
                    INSERT INTO {artifacts} (job_id, report_json, report_sha256)
                    VALUES (%s::uuid, %s::jsonb, %s)
                    ON CONFLICT (job_id) DO UPDATE SET report_json = EXCLUDED.report_json,
                        report_sha256 = EXCLUDED.report_sha256, updated_at = NOW()
                """), (job_id, json.dumps(payload, ensure_ascii=False), digest))
        return {"report_ref": f"review-report:{job_id}", "report_sha256": digest}

    def load_report_artifact(self, job_id: str):
        from app.services.report import InterviewReport
        with self.control.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(self._sql("SELECT report_json FROM {artifacts} WHERE job_id = %s::uuid"), (job_id,))
                row = cursor.fetchone()
        if row is None:
            raise ValueError("review report artifact not found")
        return InterviewReport.model_validate(row[0])

    def reusable_question_ids(self, session_id: str, manifest: dict, graph_schema_version: str) -> list[str]:
        expected = {item["question_id"]: item["input_sha256"] for item in manifest["questions"]}
        with self.control.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(self._sql("""
                    SELECT question_id, question_input_sha256 FROM {question_evaluations}
                    WHERE session_id = %s AND status = 'completed'
                      AND review_input_sha256 = %s AND review_graph_schema_version = %s
                      AND output_sha256 IS NOT NULL
                """), (session_id, manifest["input_sha256"], graph_schema_version))
                return [row[0] for row in cursor.fetchall() if expected.get(row[0]) == row[1]]

    def fail_review(self, job_id: str, error_code: str) -> None:
        with self.control.connection() as connection:
            with connection.cursor() as cursor:
                run = self._get_run(cursor, job_id, lock=True)
                cursor.execute(self._sql("""
                    UPDATE {runs} SET status = 'failed', error_code = %s, updated_at = NOW()
                    WHERE job_id = %s::uuid
                """), (error_code, job_id))
                cursor.execute(self._sql("""
                    UPDATE {jobs} SET status = 'failed', last_error_code = %s,
                        lease_owner = NULL, lease_expires_at = NULL, finished_at = NOW(), updated_at = NOW()
                    WHERE job_id = %s::uuid
                """), (error_code, job_id))
                cursor.execute(self._sql("""
                    UPDATE {reports} SET status = 'failed', error = %s, progress_json = NULL,
                        failed_at = NOW(), updated_at = NOW() WHERE session_id = %s
                """), (error_code, run.session_id))

    def _get_run(self, cursor, job_id: str, *, lock: bool = False) -> ReviewRun:
        cursor.execute(self._sql("""
            SELECT job_id::text, session_id, graph_schema_version, input_sha256, status, result_sha256,
                   error_code, provider_attempt, quality_repair_count FROM {runs}
            WHERE job_id = %s::uuid
        """ + (" FOR UPDATE" if lock else "")), (job_id,))
        row = cursor.fetchone()
        if row is None:
            raise ValueError("review run not found")
        return ReviewRun(*row)

    def _ensure_schema(self) -> None:
        with self.control.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(self._sql("""
                    CREATE TABLE IF NOT EXISTS {runs} (
                        job_id UUID PRIMARY KEY REFERENCES {jobs}(job_id) ON DELETE CASCADE,
                        session_id TEXT NOT NULL REFERENCES {sessions}(session_id) ON DELETE CASCADE,
                        graph_schema_version TEXT NOT NULL, input_sha256 TEXT NOT NULL,
                        status TEXT NOT NULL CHECK (status IN ('pending', 'running', 'waiting', 'completed', 'failed')),
                        result_sha256 TEXT, error_code TEXT, provider_attempt INTEGER NOT NULL DEFAULT 1,
                        quality_repair_count INTEGER NOT NULL DEFAULT 0, completed_at TIMESTAMPTZ,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                """))
                cursor.execute(self._sql("""
                    CREATE TABLE IF NOT EXISTS {artifacts} (
                        job_id UUID PRIMARY KEY REFERENCES {jobs}(job_id) ON DELETE CASCADE,
                        report_json JSONB NOT NULL, report_sha256 TEXT NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                """))

    def _sql(self, statement: str):
        _, sql = self.control._import_psycopg2()
        return sql.SQL(statement).format(runs=sql.Identifier(self.runs_table), artifacts=sql.Identifier(self.artifacts_table), jobs=sql.Identifier(self.jobs_table), sessions=sql.Identifier(self.sessions_table), reports=sql.Identifier(self.reports_table), question_evaluations=sql.Identifier(f"{self.table_prefix}_question_evaluations"), outbox=sql.Identifier(self.control.outbox_table))
