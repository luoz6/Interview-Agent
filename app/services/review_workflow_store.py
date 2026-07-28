from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from threading import Event, Lock, Thread
from uuid import uuid4

from app.services.postgres_connections import ConnectionProvider
from app.services.postgres_identifiers import (
    runtime_schema_identifier,
    validate_runtime_table_prefix,
)
from app.services.postgres_schema import resolve_schema_mode, validate_relations
from app.services.postgres_runtime_control import PostgresRuntimeControlStore
from app.services.runtime_domain_events import ReviewRetryDueEvent
from app.services.review_execution import current_review_execution_lease
from app.services.workflow_thread_lock import (
    FencedWriteRejected,
    ReportCommitConflict,
    ReportLeaseLost,
    ReviewEffectBusy,
    ReviewEffectConflict,
    ReviewEffectLeaseLost,
)


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


@dataclass(frozen=True)
class ReviewEffectClaim:
    operation_key: str
    job_id: str
    status: str
    effect_type: str
    question_id: str | None
    graph_schema_version: str
    input_sha256: str
    output_sha256: str | None
    payload: dict | None
    claim_token: str | None
    fencing_version: int
    worker_id: str
    job_lease_token: str


class ReviewEffectHeartbeat:
    def __init__(self, store, claim: ReviewEffectClaim, lease_seconds: int):
        self.store = store
        self.claim = claim
        self.lease_seconds = lease_seconds
        self.interval_seconds = max(0.1, lease_seconds / 3)
        self._stop = Event()
        self._lost = Event()
        self._failure_lock = Lock()
        self._failure: Exception | None = None
        self._thread: Thread | None = None

    def __enter__(self):
        self._thread = Thread(
            target=self._run,
            name="review-effect-heartbeat",
            daemon=True,
        )
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc, traceback):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(1.0, self.interval_seconds * 2))

    def ensure_owned(self):
        if self._lost.is_set():
            error = ReviewEffectLeaseLost("review effect claim was lost")
            with self._failure_lock:
                failure = self._failure
            if failure is not None:
                raise error from failure
            raise error

    def _mark_lost(self, failure: Exception | None = None) -> None:
        with self._failure_lock:
            if self._lost.is_set():
                return
            self._failure = failure
            self._lost.set()

    def _run(self):
        try:
            while not self._stop.wait(self.interval_seconds):
                if not self.store.heartbeat_effect(
                    self.claim, lease_seconds=self.lease_seconds
                ):
                    self._mark_lost()
                    return
        except Exception as exc:
            self._mark_lost(exc)


class PostgresReviewWorkflowStore:
    def __init__(
        self,
        *,
        dsn: str | None = None,
        connection_provider: ConnectionProvider | None = None,
        table_prefix: str = "interview",
        effect_lease_seconds: int = 300,
        schema_mode: str | None = None,
    ) -> None:
        validate_runtime_table_prefix(table_prefix)
        if connection_provider is None and not dsn:
            raise ValueError("dsn or connection_provider is required")
        self.dsn = dsn or ""
        self.table_prefix = table_prefix
        self.sessions_table = f"{table_prefix}_sessions"
        self.reports_table = f"{table_prefix}_reports"
        self.jobs_table = f"{table_prefix}_report_jobs"
        self.runs_table = f"{table_prefix}_review_runs"
        self.artifacts_table = f"{table_prefix}_review_artifacts"
        self.effects_table = f"{table_prefix}_review_effects"
        self.effect_lease_seconds = effect_lease_seconds
        provider_is_owned = connection_provider is None
        resolved_schema_mode = resolve_schema_mode(
            schema_mode, provider_is_owned=provider_is_owned
        )
        self.control = PostgresRuntimeControlStore(
            dsn=dsn,
            connection_provider=connection_provider,
            table_prefix=table_prefix,
            schema_mode=resolved_schema_mode,
        )
        self._connection_provider = self.control._connection_provider
        self.schema_mode = resolved_schema_mode
        if self.schema_mode == "migrate":
            self._ensure_schema()
        else:
            validate_relations(
                self._connection_provider,
                (
                    self.runs_table,
                    self.artifacts_table,
                    self.effects_table,
                    f"{table_prefix}_schema_migrations",
                ),
            )

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

    def get_run(self, job_id: str) -> ReviewRun:
        with self.control.connection() as connection:
            with connection.cursor() as cursor:
                return self._get_run(cursor, job_id)

    def schedule_retry(self, *, job_id: str, next_attempt_number: int, delay_seconds: float) -> str:
        event_id = f"review-{job_id}-retry-{next_attempt_number}"
        with self.control.connection() as connection:
            with connection.cursor() as cursor:
                lease = current_review_execution_lease(job_id)
                self._assert_active_lease(cursor, lease)
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
                lease = current_review_execution_lease(job_id)
                self._assert_active_lease(cursor, lease)
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
                    UPDATE {jobs} SET status = 'completed', lease_owner = NULL,
                        lease_token = NULL, lease_expires_at = NULL,
                        finished_at = NOW(), updated_at = NOW()
                    WHERE job_id = %s::uuid
                      AND status = 'running'
                      AND lease_owner = %s
                      AND lease_token = %s::uuid
                      AND lease_expires_at > NOW()
                """), (job_id, lease.worker_id, lease.lease_token))
                if cursor.rowcount != 1:
                    raise ReportLeaseLost(
                        "report job lease was lost before final commit"
                    )
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
                    ON CONFLICT (job_id) DO NOTHING
                """), (job_id, json.dumps(payload, ensure_ascii=False), digest))
                if cursor.rowcount == 0:
                    cursor.execute(
                        self._sql(
                            "SELECT report_sha256 FROM {artifacts} WHERE job_id = %s::uuid"
                        ),
                        (job_id,),
                    )
                    if cursor.fetchone() != (digest,):
                        raise ReviewEffectConflict(
                            "completed report artifact cannot be overwritten"
                        )
        return {"report_ref": f"review-report:{job_id}", "report_sha256": digest}

    def run_effect(
        self,
        *,
        operation_key: str,
        job_id: str,
        effect_type: str,
        graph_schema_version: str,
        input_sha256: str,
        provider,
        question_id: str | None = None,
    ) -> dict:
        claim = self.claim_effect(
            operation_key=operation_key,
            job_id=job_id,
            effect_type=effect_type,
            graph_schema_version=graph_schema_version,
            input_sha256=input_sha256,
            question_id=question_id,
        )
        if claim.status == "completed":
            return {
                "operation_key": claim.operation_key,
                "output_sha256": claim.output_sha256,
                "payload": claim.payload,
            }
        if claim.status != "running" or claim.claim_token is None:
            raise ReviewEffectBusy("review effect is owned by another executor")
        with ReviewEffectHeartbeat(
            self, claim, self.effect_lease_seconds
        ) as heartbeat:
            try:
                payload = provider()
            except Exception:
                heartbeat.ensure_owned()
                self.fail_effect(claim)
                raise
            if not isinstance(payload, dict):
                heartbeat.ensure_owned()
                self.fail_effect(claim)
                raise TypeError("review effect provider must return a JSON object")
            heartbeat.ensure_owned()
            return self.complete_effect(claim, payload)

    def claim_effect(
        self,
        *,
        operation_key: str,
        job_id: str,
        effect_type: str,
        graph_schema_version: str,
        input_sha256: str,
        question_id: str | None = None,
    ) -> ReviewEffectClaim:
        lease = current_review_execution_lease(job_id)
        fresh_token = str(uuid4())
        with self.control.connection() as connection:
            with connection.cursor() as cursor:
                self._assert_active_lease(cursor, lease)
                cursor.execute(
                    self._sql(
                        """
                        SELECT operation_key, job_id::text, status, effect_type,
                               question_id, graph_schema_version, input_sha256,
                               output_sha256, payload_json,
                               claim_token::text, fencing_version,
                               claim_expires_at > NOW()
                        FROM {effects}
                        WHERE operation_key = %s
                        FOR UPDATE
                        """
                    ),
                    (operation_key,),
                )
                row = cursor.fetchone()
                if row is None:
                    cursor.execute(
                        self._sql(
                            """
                            INSERT INTO {effects} (
                                operation_key, job_id, status, effect_type,
                                question_id, graph_schema_version, input_sha256,
                                claim_owner, claim_token, claim_expires_at,
                                fencing_version
                            )
                            VALUES (
                                %s, %s::uuid, 'running', %s, %s, %s, %s,
                                %s, %s::uuid,
                                NOW() + (%s * INTERVAL '1 second'), 1
                            )
                            ON CONFLICT (operation_key) DO NOTHING
                            RETURNING operation_key, job_id::text, status,
                                      effect_type, question_id,
                                      graph_schema_version, input_sha256,
                                      output_sha256, payload_json,
                                      claim_token::text, fencing_version, TRUE
                            """
                        ),
                        (
                            operation_key,
                            job_id,
                            effect_type,
                            question_id,
                            graph_schema_version,
                            input_sha256,
                            lease.worker_id,
                            fresh_token,
                            self.effect_lease_seconds,
                        ),
                    )
                    row = cursor.fetchone()
                    if row is None:
                        raise ReviewEffectBusy(
                            "review effect claim changed concurrently"
                        )
                self._validate_effect_identity(
                    row,
                    job_id=job_id,
                    effect_type=effect_type,
                    question_id=question_id,
                    graph_schema_version=graph_schema_version,
                    input_sha256=input_sha256,
                )
                if row[2] == "completed":
                    return self._effect_claim(row, lease)
                if row[2] == "running" and row[11]:
                    if row[9] == fresh_token:
                        return self._effect_claim(row, lease)
                    raise ReviewEffectBusy(
                        "review effect is owned by another executor"
                    )
                cursor.execute(
                    self._sql(
                        """
                        UPDATE {effects}
                        SET status = 'running', claim_owner = %s,
                            claim_token = %s::uuid,
                            claim_expires_at =
                                NOW() + (%s * INTERVAL '1 second'),
                            fencing_version = fencing_version + 1,
                            updated_at = NOW()
                        WHERE operation_key = %s
                          AND status <> 'completed'
                          AND (
                              status = 'failed'
                              OR claim_expires_at <= NOW()
                          )
                        RETURNING operation_key, job_id::text, status,
                                  effect_type, question_id,
                                  graph_schema_version, input_sha256,
                                  output_sha256, payload_json,
                                  claim_token::text, fencing_version, TRUE
                        """
                    ),
                    (
                        lease.worker_id,
                        fresh_token,
                        self.effect_lease_seconds,
                        operation_key,
                    ),
                )
                reclaimed = cursor.fetchone()
                if reclaimed is None:
                    raise ReviewEffectBusy(
                        "review effect is owned by another executor"
                    )
                return self._effect_claim(reclaimed, lease)

    def complete_effect(
        self, claim: ReviewEffectClaim, payload: dict
    ) -> dict:
        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        lease = current_review_execution_lease(claim.job_id)
        with self.control.connection() as connection:
            with connection.cursor() as cursor:
                self._assert_active_lease(cursor, lease)
                cursor.execute(
                    self._sql(
                        """
                        UPDATE {effects}
                        SET status = 'completed', output_sha256 = %s,
                            payload_json = %s::jsonb, completed_at = NOW(),
                            updated_at = NOW()
                        WHERE operation_key = %s
                          AND status = 'running'
                          AND claim_token = %s::uuid
                          AND fencing_version = %s
                          AND claim_expires_at > NOW()
                        """
                    ),
                    (
                        digest,
                        canonical,
                        claim.operation_key,
                        claim.claim_token,
                        claim.fencing_version,
                    ),
                )
                if cursor.rowcount != 1:
                    raise FencedWriteRejected(
                        "stale review effect completion was rejected"
                    )
        return {
            "operation_key": claim.operation_key,
            "output_sha256": digest,
            "payload": payload,
        }

    def fail_effect(self, claim: ReviewEffectClaim) -> None:
        lease = current_review_execution_lease(claim.job_id)
        with self.control.connection() as connection:
            with connection.cursor() as cursor:
                self._assert_active_lease(cursor, lease)
                cursor.execute(
                    self._sql(
                        """
                        UPDATE {effects}
                        SET status = 'failed', claim_expires_at = NULL,
                            updated_at = NOW()
                        WHERE operation_key = %s
                          AND status = 'running'
                          AND claim_token = %s::uuid
                          AND fencing_version = %s
                          AND claim_expires_at > NOW()
                        """
                    ),
                    (
                        claim.operation_key,
                        claim.claim_token,
                        claim.fencing_version,
                    ),
                )
                if cursor.rowcount != 1:
                    raise ReviewEffectLeaseLost(
                        "review effect claim was lost before failure commit"
                    )

    def heartbeat_effect(
        self, claim: ReviewEffectClaim, *, lease_seconds: int
    ) -> bool:
        with self.control.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    self._sql(
                        """
                        UPDATE {effects} AS effects
                        SET claim_expires_at =
                                NOW() + (%s * INTERVAL '1 second'),
                            updated_at = NOW()
                        FROM {jobs} AS jobs
                        WHERE effects.operation_key = %s
                          AND effects.status = 'running'
                          AND effects.claim_token = %s::uuid
                          AND effects.fencing_version = %s
                          AND effects.claim_expires_at > NOW()
                          AND jobs.job_id = effects.job_id
                          AND jobs.status = 'running'
                          AND jobs.lease_owner = %s
                          AND jobs.lease_token = %s::uuid
                          AND jobs.lease_expires_at > NOW()
                        """
                    ),
                    (
                        lease_seconds,
                        claim.operation_key,
                        claim.claim_token,
                        claim.fencing_version,
                        claim.worker_id,
                        claim.job_lease_token,
                    ),
                )
                return cursor.rowcount == 1

    def load_effect_payload(self, operation_key: str) -> dict:
        with self.control.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    self._sql(
                        """
                        SELECT payload_json FROM {effects}
                        WHERE operation_key = %s AND status = 'completed'
                        """
                    ),
                    (operation_key,),
                )
                row = cursor.fetchone()
        if row is None:
            raise ValueError("completed review effect not found")
        return row[0]

    @staticmethod
    def _effect_claim(row, lease) -> ReviewEffectClaim:
        return ReviewEffectClaim(
            operation_key=row[0],
            job_id=row[1],
            status=row[2],
            effect_type=row[3],
            question_id=row[4],
            graph_schema_version=row[5],
            input_sha256=row[6],
            output_sha256=row[7],
            payload=row[8],
            claim_token=row[9],
            fencing_version=int(row[10]),
            worker_id=lease.worker_id,
            job_lease_token=lease.lease_token,
        )

    @staticmethod
    def _validate_effect_identity(
        row,
        *,
        job_id: str,
        effect_type: str,
        question_id: str | None,
        graph_schema_version: str,
        input_sha256: str,
    ) -> None:
        if (
            row[1] != job_id
            or row[3] != effect_type
            or row[4] != question_id
            or row[5] != graph_schema_version
            or row[6] != input_sha256
        ):
            raise ReviewEffectConflict(
                "review effect operation identity conflicts"
            )

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
                lease = current_review_execution_lease(job_id)
                self._assert_active_lease(cursor, lease)
                run = self._get_run(cursor, job_id, lock=True)
                cursor.execute(self._sql("""
                    UPDATE {runs} SET status = 'failed', error_code = %s, updated_at = NOW()
                    WHERE job_id = %s::uuid
                """), (error_code, job_id))
                cursor.execute(self._sql("""
                    UPDATE {jobs} SET status = 'failed', last_error_code = %s,
                        lease_owner = NULL, lease_token = NULL,
                        lease_expires_at = NULL, finished_at = NOW(), updated_at = NOW()
                    WHERE job_id = %s::uuid
                      AND status = 'running'
                      AND lease_owner = %s
                      AND lease_token = %s::uuid
                      AND lease_expires_at > NOW()
                """), (
                    error_code,
                    job_id,
                    lease.worker_id,
                    lease.lease_token,
                ))
                if cursor.rowcount != 1:
                    raise ReportLeaseLost(
                        "report job lease was lost before failure commit"
                    )
                cursor.execute(self._sql("""
                    UPDATE {reports} SET status = 'failed', error = %s, progress_json = NULL,
                        failed_at = NOW(), updated_at = NOW() WHERE session_id = %s
                """), (error_code, run.session_id))

    def _assert_active_lease(self, cursor, lease) -> None:
        cursor.execute(
            self._sql(
                """
                SELECT 1 FROM {jobs}
                WHERE job_id = %s::uuid
                  AND status = 'running'
                  AND lease_owner = %s
                  AND lease_token = %s::uuid
                  AND lease_expires_at > NOW()
                FOR UPDATE
                """
            ),
            (lease.job_id, lease.worker_id, lease.lease_token),
        )
        if cursor.fetchone() is None:
            raise ReportLeaseLost("report job lease is no longer owned")

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
                cursor.execute(self._sql("CREATE INDEX IF NOT EXISTS {runs_status_index} ON {runs} (status, updated_at)"))
                cursor.execute(self._sql("CREATE INDEX IF NOT EXISTS {runs_session_index} ON {runs} (session_id, status)"))
                cursor.execute(self._sql("""
                    CREATE TABLE IF NOT EXISTS {artifacts} (
                        job_id UUID PRIMARY KEY REFERENCES {jobs}(job_id) ON DELETE CASCADE,
                        report_json JSONB NOT NULL, report_sha256 TEXT NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                """))
                cursor.execute(self._sql("""
                    CREATE TABLE IF NOT EXISTS {effects} (
                        operation_key TEXT PRIMARY KEY,
                        job_id UUID NOT NULL REFERENCES {jobs}(job_id)
                            ON DELETE CASCADE,
                        status TEXT NOT NULL CHECK (
                            status IN ('running', 'completed', 'failed')
                        ),
                        effect_type TEXT NOT NULL,
                        question_id TEXT,
                        graph_schema_version TEXT NOT NULL,
                        input_sha256 TEXT NOT NULL,
                        output_sha256 TEXT,
                        payload_json JSONB,
                        claim_owner TEXT,
                        claim_token UUID,
                        claim_expires_at TIMESTAMPTZ,
                        fencing_version BIGINT NOT NULL DEFAULT 0,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        completed_at TIMESTAMPTZ,
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                """))
                cursor.execute(self._sql("""
                    CREATE INDEX IF NOT EXISTS {effects_job_status_index}
                    ON {effects} (job_id, status)
                """))

    def _sql(self, statement: str):
        _, sql = self.control._import_psycopg2()
        return sql.SQL(statement).format(runs=sql.Identifier(self.runs_table), artifacts=sql.Identifier(self.artifacts_table), effects=sql.Identifier(self.effects_table), effects_job_status_index=sql.Identifier(runtime_schema_identifier(self.table_prefix, "review_effects_job_status_idx")), runs_status_index=sql.Identifier(runtime_schema_identifier(self.table_prefix, "review_runs_status_updated_idx")), runs_session_index=sql.Identifier(runtime_schema_identifier(self.table_prefix, "review_runs_session_status_idx")), jobs=sql.Identifier(self.jobs_table), sessions=sql.Identifier(self.sessions_table), reports=sql.Identifier(self.reports_table), question_evaluations=sql.Identifier(f"{self.table_prefix}_question_evaluations"), outbox=sql.Identifier(self.control.outbox_table))
