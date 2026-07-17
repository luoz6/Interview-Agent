import argparse
import json
from typing import Any

from app.services.config import (
    get_postgres_dsn,
    get_runtime_table_prefix,
)
from app.services.postgres_runtime_control import (
    PostgresRuntimeControlStore,
)
from app.services.postgres_session import PostgresInterviewSessionStore
from app.services.report_jobs import PostgresReportJobStore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inspect and replay durable runtime work"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    list_parser = subparsers.add_parser("list")
    list_parser.add_argument("--status", default="dead_letter")
    list_parser.add_argument("--limit", type=int, default=50)
    replay_parser = subparsers.add_parser("replay-event")
    replay_parser.add_argument("--event-id", required=True)
    report_parser = subparsers.add_parser("requeue-report")
    report_parser.add_argument("--session-id", required=True)
    return parser


def build_stores():
    dsn = get_postgres_dsn()
    prefix = get_runtime_table_prefix()
    PostgresInterviewSessionStore(dsn=dsn, table_prefix=prefix)
    return (
        PostgresRuntimeControlStore(
            dsn=dsn,
            table_prefix=prefix,
        ),
        PostgresReportJobStore(
            dsn=dsn,
            table_prefix=prefix,
        ),
    )


def execute_command(args, *, control_store, job_store) -> dict:
    if args.command == "list":
        if args.limit < 1 or args.limit > 100:
            raise ValueError("invalid recovery limit")
        return {
            "status": "ok",
            "items": control_store.list_recovery_events(
                status=args.status,
                limit=args.limit,
            ),
        }
    if args.command == "replay-event":
        return {
            "status": "ok",
            "event": _public_event(
                control_store.replay_dead_letter(args.event_id)
            ),
        }
    if args.command == "requeue-report":
        return {
            "status": "ok",
            "report_job": _public_report_job(
                job_store.requeue_failed(args.session_id)
            ),
        }
    raise ValueError("unsupported recovery command")


def main(argv=None, *, stores_factory=build_stores) -> int:
    args = build_parser().parse_args(argv)
    try:
        control_store, job_store = stores_factory()
        result = execute_command(
            args,
            control_store=control_store,
            job_store=job_store,
        )
    except ValueError:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "error_code": "invalid_recovery_state",
                }
            )
        )
        return 1
    except Exception:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "error_code": "recovery_store_unavailable",
                }
            )
        )
        return 1
    print(json.dumps(result, default=_json_default, sort_keys=True))
    return 0


def _public_event(row: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "event_id",
        "session_id",
        "correlation_id",
        "event_type",
        "status",
        "attempt_count",
        "max_attempts",
        "replay_count",
        "last_error_code",
        "available_at",
        "created_at",
        "updated_at",
        "published_at",
        "dead_lettered_at",
    )
    return {key: row.get(key) for key in keys}


def _public_report_job(row: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "job_id",
        "session_id",
        "status",
        "attempt_count",
        "max_attempts",
        "replay_count",
        "last_error_code",
        "queued_at",
        "started_at",
        "finished_at",
        "updated_at",
    )
    return {key: row.get(key) for key in keys}


def _json_default(value):
    isoformat = getattr(value, "isoformat", None)
    if isoformat is not None:
        return isoformat()
    raise TypeError(f"unsupported JSON value: {type(value).__name__}")


if __name__ == "__main__":
    raise SystemExit(main())
