import os
from uuid import uuid4

import pytest

from app.services.postgres_runtime_control import PostgresRuntimeControlStore
from app.services.postgres_session import PostgresInterviewSessionStore
from app.services.prep import InterviewPlan, InterviewQuestion
from app.services.runtime_domain_events import RoundClosedEvent


pytestmark = pytest.mark.pg_control


def require_dsn() -> str:
    dsn = os.getenv("POSTGRES_DSN")
    if not dsn:
        pytest.skip("POSTGRES_DSN is required for pg_control tests")
    return dsn


def make_plan() -> InterviewPlan:
    return InterviewPlan(
        title="Runtime control interview",
        questions=[
            InterviewQuestion(
                id="q1",
                kind="technical",
                prompt="Explain durable event delivery.",
                focus="transactional outbox",
            )
        ],
    )


def drop_runtime_tables(dsn: str, prefix: str) -> None:
    psycopg2, sql = PostgresRuntimeControlStore._import_psycopg2()
    names = [
        f"{prefix}_runtime_event_receipts",
        f"{prefix}_agent_runs",
        f"{prefix}_runtime_outbox",
        f"{prefix}_question_evaluations",
        f"{prefix}_reports",
        f"{prefix}_messages",
        f"{prefix}_sessions",
    ]
    with psycopg2.connect(dsn) as connection:
        with connection.cursor() as cursor:
            for name in names:
                cursor.execute(
                    sql.SQL("DROP TABLE IF EXISTS {table}").format(
                        table=sql.Identifier(name)
                    )
                )


@pytest.fixture
def stores():
    dsn = require_dsn()
    prefix = "test_control_" + uuid4().hex[:12]
    session = PostgresInterviewSessionStore(dsn=dsn, table_prefix=prefix)
    control = PostgresRuntimeControlStore(dsn=dsn, table_prefix=prefix)
    turn = session.start(
        make_plan(),
        job_description="Backend reliability role",
        resume_text="Built durable Python workers",
        job_tags=["python", "postgresql"],
    )
    yield {
        "dsn": dsn,
        "prefix": prefix,
        "session": session,
        "control": control,
        "session_id": turn.session_id,
    }
    drop_runtime_tables(dsn, prefix)


def make_round_event(session_id: str) -> RoundClosedEvent:
    return RoundClosedEvent(
        event_id="event-control-1",
        session_id=session_id,
        correlation_id="prep-control-1",
        causation_id="cmd-control-1",
        state_version=2,
        question_id="q1",
        answer_state="skipped",
        job_tags=["python", "postgresql"],
    )


def test_schema_has_cascading_session_foreign_keys(stores):
    control = stores["control"]

    assert control.list_foreign_keys() == {
        control.outbox_table: ("session_id", "CASCADE"),
        control.receipts_table: ("session_id", "CASCADE"),
        control.agent_runs_table: ("session_id", "CASCADE"),
    }


def test_enqueue_is_idempotent_by_event_id(stores):
    control = stores["control"]
    event = make_round_event(stores["session_id"])

    with control.connection() as connection:
        with connection.cursor() as cursor:
            assert control.enqueue_event(cursor, event) is True
            assert control.enqueue_event(cursor, event) is False

    assert control.count_outbox(event.event_id) == 1


def test_claim_batch_leases_pending_event_and_increments_attempt(stores):
    control = stores["control"]
    event = make_round_event(stores["session_id"])
    with control.connection() as connection:
        with connection.cursor() as cursor:
            control.enqueue_event(cursor, event)

    claims = control.claim_batch(
        worker_id="dispatcher-1",
        limit=20,
        lease_seconds=60,
    )

    assert len(claims) == 1
    assert claims[0]["event_id"] == event.event_id
    assert claims[0]["status"] == "running"
    assert claims[0]["attempt_count"] == 1
    assert claims[0]["lease_owner"] == "dispatcher-1"
    assert claims[0]["payload"]["question_id"] == "q1"


def test_guarded_completion_requires_matching_lease_owner(stores):
    control = stores["control"]
    event = make_round_event(stores["session_id"])
    with control.connection() as connection:
        with connection.cursor() as cursor:
            control.enqueue_event(cursor, event)
    control.claim_batch(worker_id="dispatcher-1", limit=1, lease_seconds=60)

    assert control.mark_published(event.event_id, "dispatcher-2") is None
    published = control.mark_published(event.event_id, "dispatcher-1")

    assert published["status"] == "published"
    assert published["lease_owner"] is None
