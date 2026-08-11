"""PostgreSQL integration coverage."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from inspect import getsource
from threading import Barrier

import pytest

from app.services.interview_generation_store import (
    PostgresInterviewGenerationStore,
)
from app.services.interview_workflow_store import (
    PostgresInterviewWorkflowStore,
)
from app.services.langgraph_canary_status import (
    PostgresLangGraphCanaryStatusService,
)
from app.services.postgres_runtime_control import PostgresRuntimeControlStore
from app.services.postgres_session import PostgresInterviewSessionStore
from app.services.report_jobs import PostgresReportJobStore
from app.services.review_workflow_store import PostgresReviewWorkflowStore
from app.services.runtime_signal_metrics import PostgresRuntimeSignalStore
from tests.postgres_support import (
    assert_safe_test_prefix,
    make_runtime_table_prefix,
)
from tests.integration.postgres.test_postgres_session_store import make_plan


pytestmark = pytest.mark.pg_control


def _drop_prefix(dsn: str, prefix: str) -> None:
    assert_safe_test_prefix(prefix)
    psycopg2, sql = PostgresRuntimeSignalStore._import_psycopg2()
    suffixes = (
        "runtime_signal_buckets",
        "review_effects",
        "review_artifacts",
        "review_runs",
        "generation_chunks",
        "generation_attempts",
        "generations",
        "workflow_commands",
        "runtime_event_receipts",
        "runtime_outbox",
        "agent_runs",
        "report_jobs",
        "reports",
        "question_evaluations",
        "messages",
        "sessions",
    )
    with psycopg2.connect(dsn) as connection:
        with connection.cursor() as cursor:
            for suffix in suffixes:
                cursor.execute(
                    sql.SQL("DROP TABLE IF EXISTS {table} CASCADE").format(
                        table=sql.Identifier(f"{prefix}_{suffix}")
                    )
                )


def _initialize_canary_tables(dsn: str, prefix: str):
    sessions = PostgresInterviewSessionStore(dsn=dsn, table_prefix=prefix)
    control = PostgresRuntimeControlStore(dsn=dsn, table_prefix=prefix)
    PostgresInterviewWorkflowStore(dsn=dsn, table_prefix=prefix)
    PostgresInterviewGenerationStore(dsn=dsn, table_prefix=prefix)
    PostgresReportJobStore(dsn=dsn, table_prefix=prefix)
    PostgresReviewWorkflowStore(dsn=dsn, table_prefix=prefix)
    signals = PostgresRuntimeSignalStore(dsn=dsn, table_prefix=prefix)
    return sessions, control, signals


def _short_prefix() -> str:
    # Keep every derived identifier below PostgreSQL's 63-byte limit. The
    # shared fixture includes the full test name and is intentionally longer.
    return make_runtime_table_prefix("s47signal")


def test_signal_bucket_schema_is_closed_and_privacy_safe(
    postgres_dsn,
):
    prefix = _short_prefix()
    try:
        store = PostgresRuntimeSignalStore(
            dsn=postgres_dsn, table_prefix=prefix
        )
        psycopg2, _ = store._import_psycopg2()
        with psycopg2.connect(postgres_dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema = current_schema()
                      AND table_name = %s
                    ORDER BY ordinal_position
                    """,
                    (store.table,),
                )
                columns = [row[0] for row in cursor.fetchall()]

        assert columns == [
            "bucket_start",
            "workflow_type",
            "signal_code",
            "signal_count",
            "updated_at",
        ]
        forbidden_fragments = (
            "session",
            "job",
            "thread",
            "token",
            "payload",
            "message",
            "answer",
            "report",
            "hash",
            "checkpoint",
            "error_detail",
        )
        assert all(
            fragment not in column
            for column in columns
            for fragment in forbidden_fragments
        )
    finally:
        _drop_prefix(postgres_dsn, prefix)


def test_signal_bucket_concurrent_increments_are_not_lost(
    postgres_dsn,
):
    prefix = _short_prefix()
    workers = 8
    try:
        store = PostgresRuntimeSignalStore(
            dsn=postgres_dsn, table_prefix=prefix
        )
        barrier = Barrier(workers)

        def increment_once():
            barrier.wait(timeout=5)
            store.increment(
                workflow_type="interview",
                signal_code="workflow_thread_busy",
            )

        with ThreadPoolExecutor(max_workers=workers) as executor:
            list(executor.map(lambda _: increment_once(), range(workers)))

        counts = store.sum_since(
            datetime.now(timezone.utc) - timedelta(minutes=1)
        )
        assert counts == {"workflow_thread_busy": workers}
    finally:
        _drop_prefix(postgres_dsn, prefix)


def test_canary_snapshot_counts_all_three_unfinished_outbox_states(
    postgres_dsn,
):
    prefix = _short_prefix()
    try:
        sessions, control, signals = _initialize_canary_tables(
            postgres_dsn, prefix
        )
        session = sessions.start(
            make_plan(),
            job_description="role",
            resume_text="resume",
            job_tags=["python"],
        )
        psycopg2, sql = control._import_psycopg2()
        with psycopg2.connect(postgres_dsn) as connection:
            with connection.cursor() as cursor:
                for status in (
                    "pending",
                    "retrying",
                    "running",
                    "published",
                    "dead_letter",
                ):
                    cursor.execute(
                        sql.SQL(
                            """
                            INSERT INTO {outbox} (
                                event_id, session_id, correlation_id,
                                event_type, schema_version, payload_json,
                                status, lease_owner, lease_expires_at,
                                created_at, updated_at
                            ) VALUES (
                                %s, %s, %s, 'interview_command_ready',
                                'runtime-event-v1', '{{}}'::jsonb, %s,
                                CASE WHEN %s = 'running' THEN 'worker' END,
                                CASE WHEN %s = 'running'
                                     THEN NOW() - INTERVAL '60 seconds' END,
                                NOW() - INTERVAL '90 seconds', NOW()
                            )
                            """
                        ).format(outbox=sql.Identifier(control.outbox_table)),
                        (
                            f"event-{status}",
                            session.session_id,
                            f"correlation-{status}",
                            status,
                            status,
                            status,
                        ),
                    )
        signals.increment(
            workflow_type="interview",
            signal_code="projection_conflict",
        )
        signals.increment(
            workflow_type="interview",
            signal_code="workflow_thread_busy",
        )

        snapshot = PostgresLangGraphCanaryStatusService(
            dsn=postgres_dsn,
            table_prefix=prefix,
        ).snapshot(
            observed_since=(
                datetime.now(timezone.utc) - timedelta(minutes=5)
            ),
            phase="baseline",
            interview_rollout_percent=0,
            review_rollout_percent=0,
            lease_expiry_grace_seconds=30,
        )

        assert snapshot.outbox_pending_count == 1
        assert snapshot.outbox_retrying_count == 1
        assert snapshot.outbox_running_count == 1
        assert snapshot.oldest_unfinished_outbox_age_seconds >= 89
        assert snapshot.expired_running_outbox_lease_count == 1
        assert snapshot.projection_divergence_count == 1
        assert snapshot.workflow_thread_busy_count == 1
        assert snapshot.schema_version == "langgraph-canary-v2"
        assert snapshot.phase == "baseline"
    finally:
        _drop_prefix(postgres_dsn, prefix)


def test_canary_outbox_sql_contains_no_processing_status():
    source = getsource(PostgresLangGraphCanaryStatusService)

    assert "status IN ('pending', 'processing')" not in source
    assert "status IN ('pending', 'retrying', 'running')" in source


def test_canary_signal_window_and_outbox_status_have_index_plans(
    postgres_dsn,
):
    prefix = _short_prefix()
    try:
        sessions, control, signals = _initialize_canary_tables(
            postgres_dsn, prefix
        )
        session = sessions.start(
            make_plan(),
            job_description="role",
            resume_text="resume",
            job_tags=["python"],
        )
        psycopg2, sql = control._import_psycopg2()
        with psycopg2.connect(postgres_dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        """
                        INSERT INTO {signals} (
                            bucket_start, workflow_type, signal_code,
                            signal_count
                        )
                        SELECT
                            date_trunc('minute', NOW()) -
                                (series * INTERVAL '1 minute'),
                            'interview', 'workflow_thread_busy', 1
                        FROM generate_series(1, 2000) AS series
                        """
                    ).format(signals=sql.Identifier(signals.table))
                )
                cursor.execute(
                    sql.SQL(
                        """
                        INSERT INTO {outbox} (
                            event_id, session_id, correlation_id,
                            event_type, schema_version, payload_json,
                            status, created_at, updated_at
                        )
                        SELECT
                            'event-' || series,
                            %s,
                            'correlation-' || series,
                            'interview_command_ready',
                            'runtime-event-v1',
                            '{{}}'::jsonb,
                            CASE WHEN series <= 10
                                 THEN 'pending' ELSE 'published' END,
                            NOW() - (series * INTERVAL '1 second'),
                            NOW()
                        FROM generate_series(1, 2000) AS series
                        """
                    ).format(outbox=sql.Identifier(control.outbox_table)),
                    (session.session_id,),
                )
                cursor.execute(
                    sql.SQL("ANALYZE {signals}").format(
                        signals=sql.Identifier(signals.table)
                    )
                )
                cursor.execute(
                    sql.SQL("ANALYZE {outbox}").format(
                        outbox=sql.Identifier(control.outbox_table)
                    )
                )
                cursor.execute(
                    sql.SQL(
                        """
                        EXPLAIN (FORMAT JSON)
                        SELECT signal_code, SUM(signal_count)
                        FROM {signals}
                        WHERE bucket_start >= NOW() - INTERVAL '5 minutes'
                        GROUP BY signal_code
                        """
                    ).format(signals=sql.Identifier(signals.table))
                )
                signal_plan = str(cursor.fetchone()[0])
                cursor.execute(
                    sql.SQL(
                        """
                        EXPLAIN (FORMAT JSON)
                        SELECT COUNT(*) FROM {outbox}
                        WHERE status = 'pending'
                        """
                    ).format(outbox=sql.Identifier(control.outbox_table))
                )
                outbox_plan = str(cursor.fetchone()[0])

        assert "Index" in signal_plan
        assert "Seq Scan" not in signal_plan
        assert "Index" in outbox_plan
        assert "Seq Scan" not in outbox_plan
    finally:
        _drop_prefix(postgres_dsn, prefix)
