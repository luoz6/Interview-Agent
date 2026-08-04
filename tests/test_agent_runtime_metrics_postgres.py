from datetime import datetime, timezone
import json
import os

import pytest

from app.services.agent_recorders import (
    CompositeAgentRunRecorder,
    PostgresAgentRunRecorder,
)
from app.services.agent_runtime import (
    AgentExecutionContext,
    AgentExecutionRunner,
    AgentRunRecord,
)
from app.services.agent_trace import AgentTraceRecorder
from app.services.postgres_runtime_control import PostgresRuntimeControlStore
from app.services.postgres_session import PostgresInterviewSessionStore
from tests.postgres_support import make_runtime_table_prefix
from tests.test_runtime_signal_metrics_postgres import _drop_prefix


pytestmark = pytest.mark.pg_control


def require_dsn() -> str:
    dsn = os.getenv("POSTGRES_DSN")
    if not dsn:
        pytest.skip("POSTGRES_DSN is required for agent runtime metrics")
    return dsn


@pytest.fixture
def control():
    dsn = require_dsn()
    prefix = make_runtime_table_prefix("agent_metrics")
    PostgresInterviewSessionStore(dsn=dsn, table_prefix=prefix)
    store = PostgresRuntimeControlStore(dsn=dsn, table_prefix=prefix)
    try:
        yield store
    finally:
        _drop_prefix(dsn, prefix)


def make_record(
    run_id: str,
    *,
    agent: str,
    operation: str,
    status: str,
    started_at: str,
    latency_ms: float,
    fallback_reason: str | None = None,
) -> AgentRunRecord:
    return AgentRunRecord(
        run_id=run_id,
        correlation_id="aggregate-correlation",
        agent=agent,
        operation=operation,
        phase="review" if agent == "report_coach" else "interview",
        status=status,
        started_at=started_at,
        finished_at=started_at,
        latency_ms=latency_ms,
        fallback_reason=fallback_reason,
        output_type="str",
    )


def test_agent_run_aggregate_groups_operation_status_and_latency(control):
    rows = [
        ("r1", "completed", 10.0, None),
        ("r2", "degraded", 20.0, "provider_error"),
        ("r3", "failed", 30.0, None),
        ("r4", "cancelled", 40.0, "client_disconnected"),
    ]
    for run_id, status, latency, fallback_reason in rows:
        control.record_agent_run(
            make_record(
                run_id,
                agent="examiner",
                operation="stream_followup",
                status=status,
                started_at="2026-07-27T10:00:00Z",
                latency_ms=latency,
                fallback_reason=fallback_reason,
            )
        )
    control.record_agent_run(
        make_record(
            "outside-window",
            agent="examiner",
            operation="stream_followup",
            status="failed",
            started_at="2026-07-26T10:00:00Z",
            latency_ms=999.0,
        )
    )
    control.record_agent_run(
        make_record(
            "other-operation",
            agent="report_coach",
            operation="generate_report",
            status="completed",
            started_at="2026-07-27T10:00:00Z",
            latency_ms=50.0,
        )
    )

    result = control.aggregate_agent_runs(
        started_at=datetime(2026, 7, 27, tzinfo=timezone.utc),
        finished_before=datetime(2026, 7, 28, tzinfo=timezone.utc),
        agent="examiner",
        operation="stream_followup",
    )

    assert len(result) == 1
    item = result[0]
    assert item["agent"] == "examiner"
    assert item["operation"] == "stream_followup"
    assert item["invocation_count"] == 4
    assert item["completed_count"] == 1
    assert item["degraded_count"] == 1
    assert item["failed_count"] == 1
    assert item["cancelled_count"] == 1
    assert item["fallback_count"] == 2
    assert item["completed_rate"] == 0.25
    assert item["fallback_rate"] == 0.5
    assert item["latency_p50_ms"] == 25.0
    assert item["latency_p95_ms"] == pytest.approx(38.5)
    assert item["latency_p99_ms"] == pytest.approx(39.7)
    assert "safe_metadata" not in item
    assert "session_id" not in item
    assert "correlation_id" not in item


def test_agent_run_aggregate_empty_window_and_invalid_window(control):
    assert control.aggregate_agent_runs(
        started_at=datetime(2026, 7, 27, tzinfo=timezone.utc),
        finished_before=datetime(2026, 7, 28, tzinfo=timezone.utc),
    ) == []

    with pytest.raises(ValueError, match="finished_before"):
        control.aggregate_agent_runs(
            started_at=datetime(2026, 7, 28, tzinfo=timezone.utc),
            finished_before=datetime(2026, 7, 27, tzinfo=timezone.utc),
        )


def test_agent_operation_index_has_expected_catalog_structure(control):
    with control.connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT indexdef
                FROM pg_indexes
                WHERE schemaname = current_schema()
                  AND tablename = %s
                """,
                (control.agent_runs_table,),
            )
            definitions = [row[0] for row in cursor.fetchall()]

    matching = [
        definition
        for definition in definitions
        if "(agent, operation, started_at)" in definition
    ]
    assert len(matching) == 1


def test_runner_persists_identical_sanitized_metadata_to_file_and_postgres(
    control,
    tmp_path,
):
    runner = AgentExecutionRunner(
        recorder=CompositeAgentRunRecorder(
            [
                AgentTraceRecorder(tmp_path),
                PostgresAgentRunRecorder(control),
            ]
        )
    )
    context = AgentExecutionContext(
        correlation_id="metadata-parity",
        agent="report_coach",
        operation="generate_report",
        phase="review",
    )

    assert runner.run(
        context,
        lambda: "report",
        metadata=lambda _output: {
            "feedback_count": 2,
            "report_path": "microbatch",
            "user_prompt": "private",
            "artifact": "C:\\private\\trace.json",
        },
    ) == "report"

    trace_path = next(tmp_path.rglob("*.json"))
    file_metadata = json.loads(trace_path.read_text(encoding="utf-8"))[
        "safe_metadata"
    ]
    with control.connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT safe_metadata FROM {control.agent_runs_table} "
                "WHERE run_id = %s",
                (context.run_id,),
            )
            postgres_metadata = cursor.fetchone()[0]

    assert file_metadata == {
        "feedback_count": 2,
        "report_path": "microbatch",
    }
    assert postgres_metadata == file_metadata
