import os
from uuid import uuid4

import pytest

from app.services.agent_recorders import (
    CompositeAgentRunRecorder,
    PostgresAgentRunRecorder,
)
from app.services.agent_runtime import AgentRunRecord
from app.services.postgres_runtime_control import PostgresRuntimeControlStore
from app.services.postgres_session import PostgresInterviewSessionStore
from app.services.prep import InterviewPlan, InterviewQuestion


def make_record(session_id: str | None = None) -> AgentRunRecord:
    return AgentRunRecord(
        run_id="agent-run-1",
        correlation_id="prep-1",
        causation_id="cmd-1",
        agent="examiner",
        operation="generate_followup",
        phase="interview",
        session_id=session_id,
        question_id="q1" if session_id else None,
        state_version=2 if session_id else None,
        command_id="cmd-1" if session_id else None,
        evidence_ids=["redis-1"],
        attempt_number=2,
        status="completed",
        started_at="2026-07-17T00:00:00Z",
        finished_at="2026-07-17T00:00:00.050000Z",
        latency_ms=50,
        output_type="str",
        safe_metadata={"chunk_count": 2},
    )


class CapturingRecorder:
    def __init__(self):
        self.records = []

    def record(self, record):
        self.records.append(record)


class FailingRecorder:
    def record(self, record):
        raise RuntimeError("database detail")


def test_composite_continues_after_one_recorder_fails():
    record = make_record()
    healthy = CapturingRecorder()

    CompositeAgentRunRecorder(
        [FailingRecorder(), healthy]
    ).record(record)

    assert healthy.records == [record]


def require_dsn() -> str:
    dsn = os.getenv("POSTGRES_DSN")
    if not dsn:
        pytest.skip("POSTGRES_DSN is required for pg_control tests")
    return dsn


@pytest.fixture
def pg_control():
    dsn = require_dsn()
    prefix = "test_agent_runs_" + uuid4().hex[:10]
    session_store = PostgresInterviewSessionStore(
        dsn=dsn,
        table_prefix=prefix,
    )
    control = PostgresRuntimeControlStore(
        dsn=dsn,
        table_prefix=prefix,
    )
    turn = session_store.start(
        InterviewPlan(
            title="Agent run ledger",
            questions=[
                InterviewQuestion(
                    id="q1",
                    kind="technical",
                    prompt="Explain tracing.",
                    focus="runtime tracing",
                )
            ],
        ),
        job_description="Backend observability role",
        resume_text="Built trace pipelines",
        job_tags=["python"],
    )
    yield control, turn.session_id


@pytest.mark.pg_control
def test_postgres_insert_is_idempotent(pg_control):
    control, session_id = pg_control
    record = make_record(session_id)
    recorder = PostgresAgentRunRecorder(control)

    recorder.record(record)
    recorder.record(record)

    assert control.count_agent_runs(record.run_id) == 1


@pytest.mark.pg_control
def test_public_query_excludes_safe_metadata(pg_control):
    control, session_id = pg_control
    control.record_agent_run(make_record(session_id))

    item = control.list_agent_runs(session_id=session_id)[0]

    assert "safe_metadata" not in item
    assert item["attempt_number"] == 2
