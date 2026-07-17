import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.postgres_session import PostgresInterviewSessionStore
from app.services.session_errors import SessionVersionConflict
from tests.stage38_fakes import (
    FakeStage38InterviewLLM,
    make_stage38_plan,
    make_stage38_report,
)


DEFAULT_DSN = "postgresql://postgres:postgres@127.0.0.1:5432/interview"


@dataclass(frozen=True)
class AcceptanceCheck:
    name: str
    status: str
    detail: str


def make_store(dsn: str, table_prefix: str) -> PostgresInterviewSessionStore:
    return PostgresInterviewSessionStore(
        dsn=dsn,
        table_prefix=table_prefix,
        llm=FakeStage38InterviewLLM(),
    )


def start_session(store: PostgresInterviewSessionStore):
    return store.start(
        make_stage38_plan(),
        job_description="Backend role using FastAPI, Redis, and PostgreSQL.",
        resume_text="Built FastAPI services with Redis cache-aside and PostgreSQL.",
        job_tags=["python", "fastapi", "redis", "postgresql"],
    )


def count_messages(snapshot: dict, role: str) -> int:
    return len([message for message in snapshot["messages"] if message["role"] == role])


def run_acceptance(*, dsn: str, table_prefix: str) -> dict:
    checks: list[AcceptanceCheck] = []
    store = make_store(dsn, table_prefix)
    tables = store.list_runtime_tables()
    checks.append(
        AcceptanceCheck(
            name="schema_initializes_isolated_tables",
            status="pass",
            detail=",".join(tables),
        )
    )

    stale_session = start_session(store)
    try:
        store.submit_answer(
            stale_session.session_id,
            "I used Redis cache-aside.",
            expected_version=0,
            command_id="cmd-stale",
        )
        raise AssertionError("stale command unexpectedly succeeded")
    except SessionVersionConflict as exc:
        assert exc.expected_version == 0
        assert exc.actual_version == 1
    checks.append(
        AcceptanceCheck(
            name="stale_expected_version_rejected",
            status="pass",
            detail="expected=0 actual=1",
        )
    )

    duplicate_session = start_session(store)
    first_turn = store.submit_answer(
        duplicate_session.session_id,
        "I built a FastAPI API with Redis.",
        expected_version=1,
        command_id="cmd-answer",
    )
    duplicate_turn = store.submit_answer(
        duplicate_session.session_id,
        "I built a FastAPI API with Redis.",
        expected_version=1,
        command_id="cmd-answer",
    )
    duplicate_snapshot = store.snapshot(duplicate_session.session_id)
    assert duplicate_turn.follow_up == first_turn.follow_up
    assert duplicate_snapshot["state_version"] == 2
    assert duplicate_snapshot["checkpoint_version"] == 2
    assert duplicate_snapshot["last_command_id"] == "cmd-answer"
    assert count_messages(duplicate_snapshot, "candidate") == 1
    checks.append(
        AcceptanceCheck(
            name="duplicate_command_id_is_idempotent",
            status="pass",
            detail="state_version=2 candidate_messages=1",
        )
    )

    stream_session = start_session(store)
    prepared = store.prepare_streaming_answer(
        stream_session.session_id,
        "I protected PostgreSQL during cache misses.",
        expected_version=1,
        command_id="cmd-stream",
    )
    assert prepared.stream_follow_up is True
    finalized = store.complete_streaming_answer(
        stream_session.session_id,
        follow_up_text="Please explain cache miss protection.",
        expected_version=2,
        command_id="cmd-stream",
    )
    duplicate_finalized = store.complete_streaming_answer(
        stream_session.session_id,
        follow_up_text="Please explain cache miss protection.",
        expected_version=2,
        command_id="cmd-stream",
    )
    stream_snapshot = store.snapshot(stream_session.session_id)
    assert duplicate_finalized == finalized
    assert stream_snapshot["state_version"] == 3
    assert stream_snapshot["checkpoint_version"] == 3
    assert stream_snapshot["last_command_id"] == "cmd-stream"
    assert count_messages(stream_snapshot, "candidate") == 1
    checks.append(
        AcceptanceCheck(
            name="stream_completion_advances_version_once",
            status="pass",
            detail="state_version=3 last_command_id=cmd-stream",
        )
    )

    report_session = start_session(store)
    store.finish(
        report_session.session_id,
        expected_version=1,
        command_id="cmd-finish",
    )
    assert store.mark_report_processing(report_session.session_id) is True
    processing_snapshot = store.snapshot(report_session.session_id)
    assert processing_snapshot["phase"] == "review"
    assert processing_snapshot["phase_status"] == "active"
    assert processing_snapshot["review_status"] == "processing"
    assert processing_snapshot["state_version"] == 3
    assert processing_snapshot["last_command_id"] == "cmd-finish"
    store.save_report(
        report_session.session_id,
        make_stage38_report(report_session.session_id),
    )
    completed_snapshot = store.snapshot(report_session.session_id)
    assert completed_snapshot["phase_status"] == "completed"
    assert completed_snapshot["review_status"] == "completed"
    assert completed_snapshot["state_version"] == 4
    assert completed_snapshot["last_command_id"] == "cmd-finish"
    checks.append(
        AcceptanceCheck(
            name="report_lifecycle_preserves_user_command_id",
            status="pass",
            detail="processing_version=3 completed_version=4 last_command_id=cmd-finish",
        )
    )

    recovered = make_store(dsn, table_prefix)
    recovered_snapshot = recovered.snapshot(report_session.session_id)
    recovered_record = recovered.get_report_record(report_session.session_id)
    assert recovered_snapshot["state_version"] == 4
    assert recovered_snapshot["last_command_id"] == "cmd-finish"
    assert recovered_record is not None
    assert recovered_record.status == "completed"
    checks.append(
        AcceptanceCheck(
            name="postgres_reinstantiation_preserves_state",
            status="pass",
            detail=f"session_id={report_session.session_id}",
        )
    )

    return {
        "stage": "Stage 38 Postgres Runtime Acceptance",
        "status": "pass",
        "dsn": dsn,
        "table_prefix": table_prefix,
        "checks": [asdict(check) for check in checks],
    }


def drop_isolated_tables(*, dsn: str, table_prefix: str) -> None:
    import psycopg2
    from psycopg2 import sql

    table_names = [
        f"{table_prefix}_question_evaluations",
        f"{table_prefix}_reports",
        f"{table_prefix}_messages",
        f"{table_prefix}_sessions",
    ]
    with psycopg2.connect(dsn) as connection:
        with connection.cursor() as cursor:
            for table_name in table_names:
                cursor.execute(
                    sql.SQL("DROP TABLE IF EXISTS {} CASCADE").format(
                        sql.Identifier(table_name)
                    )
                )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dsn",
        default=os.getenv("POSTGRES_DSN", DEFAULT_DSN),
        help="PostgreSQL DSN used for the acceptance run.",
    )
    parser.add_argument(
        "--table-prefix",
        default=f"stage38_{uuid4().hex[:10]}",
        help="Isolated runtime table prefix for this run.",
    )
    parser.add_argument(
        "--write-json",
        default=None,
        help="Optional disposable output path for acceptance evidence JSON.",
    )
    parser.add_argument(
        "--keep-tables",
        action="store_true",
        help="Keep isolated stage38 tables for manual database inspection.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        result = run_acceptance(dsn=args.dsn, table_prefix=args.table_prefix)
        rendered = json.dumps(result, ensure_ascii=False, indent=2)
        print(rendered)
        if args.write_json:
            output_path = Path(args.write_json)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(rendered + "\n", encoding="utf-8")
    finally:
        if not args.keep_tables:
            drop_isolated_tables(dsn=args.dsn, table_prefix=args.table_prefix)


if __name__ == "__main__":
    main()
