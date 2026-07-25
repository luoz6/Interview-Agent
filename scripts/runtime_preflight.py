import argparse
import json
import os
import re
import subprocess
import sys
from math import ceil
from time import perf_counter
from uuid import uuid4
from urllib.parse import urlsplit, urlunsplit

from app.services.config import (
    get_interview_chunk_retention_hours,
    get_interview_langgraph_rollout_percent,
    get_interview_langgraph_runtime_enabled,
    get_interview_langgraph_version,
    get_report_langgraph_rollout_percent,
    get_report_langgraph_runtime_enabled,
    get_report_langgraph_version,
    get_postgres_dsn,
    get_redis_url,
    get_runtime_table_prefix,
)


class PreflightError(RuntimeError):
    pass


def validate_langgraph_configuration(
    *,
    runtime_store: str,
    runtime_enabled: bool,
    rollout_percent: int,
    strict_msgpack: str,
    retention_hours: int,
) -> dict[str, object]:
    if rollout_percent > 0 and (
        runtime_store != "postgres" or not runtime_enabled
    ):
        raise PreflightError(
            "LangGraph rollout requires enabled PostgreSQL runtime"
        )
    if strict_msgpack.strip().lower() != "true":
        raise PreflightError("LANGGRAPH_STRICT_MSGPACK must be true")
    if retention_hours < 1:
        raise PreflightError("chunk retention must be positive")
    return {
        "runtime_store": runtime_store,
        "runtime_enabled": runtime_enabled,
        "rollout_percent": rollout_percent,
        "strict_msgpack": True,
        "chunk_retention_hours": retention_hours,
    }


def validate_langgraph_schema_snapshot(
    *,
    tables: list[str],
    indexes: list[str],
    expected_tables: list[str],
    expected_indexes: list[str],
) -> dict[str, int]:
    if set(tables) != set(expected_tables):
        raise PreflightError("LangGraph workflow tables are incomplete")
    if not set(expected_indexes).issubset(indexes):
        raise PreflightError("LangGraph recovery indexes are incomplete")
    return {
        "workflow_tables": len(tables),
        "recovery_indexes": len(expected_indexes),
    }


def should_check_langgraph_postgres(
    *,
    runtime_store: str,
    interview_runtime_enabled: bool,
    review_runtime_enabled: bool,
    profile: str,
) -> bool:
    return (
        runtime_store == "postgres"
        and (interview_runtime_enabled or review_runtime_enabled)
        and profile == "core"
    )


def validate_registered_graph_versions(
    interview_version: str, review_version: str
) -> list[str]:
    from app.services.langgraph_runtime import VersionedGraphRegistry

    registry = VersionedGraphRegistry()
    interview_graph = object()
    review_graph = object()
    registry.register(interview_version, interview_graph)
    registry.register(review_version, review_graph)
    if registry.get(interview_version) is not interview_graph:
        raise PreflightError("Interview graph registration failed")
    if registry.get(review_version) is not review_graph:
        raise PreflightError("Review graph registration failed")
    try:
        registry.get("unsupported-preflight-version")
    except ValueError:
        pass
    else:
        raise PreflightError("unknown graph versions must fail closed")
    return [interview_version, review_version]


def check_langgraph_runtime() -> dict[str, object]:
    from app.services.interview_generation_store import (
        PostgresInterviewGenerationStore,
    )
    from app.services.interview_workflow_store import (
        PostgresInterviewWorkflowStore,
    )
    from app.services.langgraph_runtime import PostgresCheckpointerRuntime
    from app.services.postgres_session import PostgresInterviewSessionStore
    from app.services.report_jobs import PostgresReportJobStore
    from app.services.review_workflow_store import PostgresReviewWorkflowStore
    from scripts.audit_agent_runtime import audit_runtime_control_payloads

    dsn = get_postgres_dsn()
    prefix = get_runtime_table_prefix()
    session_store = PostgresInterviewSessionStore(
        dsn=dsn, table_prefix=prefix
    )
    PostgresReportJobStore(dsn=dsn, table_prefix=prefix)
    workflow_store = PostgresInterviewWorkflowStore(
        dsn=dsn, table_prefix=prefix
    )
    generation_store = PostgresInterviewGenerationStore(
        dsn=dsn, table_prefix=prefix
    )
    review_store = PostgresReviewWorkflowStore(dsn=dsn, table_prefix=prefix)
    expected_tables = [
        workflow_store.commands_table,
        generation_store.generations_table,
        generation_store.attempts_table,
        generation_store.chunks_table,
        review_store.runs_table,
        review_store.artifacts_table,
    ]
    expected_indexes = [
        f"{workflow_store.commands_table}_status_updated_idx",
        f"{session_store._runtime_control.outbox_table}_status_available_idx",
        f"{generation_store.generations_table}_session_source_idx",
        f"{generation_store.chunks_table}_replay_idx",
        f"{review_store.runs_table}_status_updated_idx",
        f"{review_store.runs_table}_session_status_idx",
    ]
    psycopg2, _ = session_store._import_psycopg2()
    with psycopg2.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = current_schema()
                  AND table_name = ANY(%s)
                """,
                (expected_tables,),
            )
            tables = [row[0] for row in cursor.fetchall()]
            cursor.execute(
                """
                SELECT indexname FROM pg_indexes
                WHERE schemaname = current_schema()
                  AND indexname = ANY(%s)
                """,
                (expected_indexes,),
            )
            indexes = [row[0] for row in cursor.fetchall()]
            cursor.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = %s
                  AND column_name = 'lease_token'
                """,
                (f"{prefix}_report_jobs",),
            )
            if cursor.fetchone() is None:
                raise PreflightError("report job lease fencing is incomplete")
    result: dict[str, object] = validate_langgraph_schema_snapshot(
        tables=tables,
        indexes=indexes,
        expected_tables=expected_tables,
        expected_indexes=expected_indexes,
    )
    checkpointer = PostgresCheckpointerRuntime(dsn)
    try:
        checkpointer.start()
        result["saver_setup"] = True
    finally:
        checkpointer.shutdown()

    privacy = audit_runtime_control_payloads(
        [
            {
                "engine": "versioned",
                "default_engine": "legacy",
                "langgraph_version": "langgraph-v1",
                "checkpoint_backend": "postgres",
                "resume_contract": "checkpointed_http_sse",
            }
        ]
    )
    if privacy["status"] != "PASS":
        raise PreflightError("runtime diagnostics privacy audit failed")
    result["privacy_allowlist"] = "PASS"
    result["graph_versions"] = validate_registered_graph_versions(
        get_interview_langgraph_version(),
        get_report_langgraph_version(),
    )
    result["consumer_event_types"] = [
        "interview_command_ready",
        "interview_retry_due",
        "round_closed",
        "review_retry_due",
    ]
    result["report_lease_fencing"] = True
    return result


def validate_runtime_versions(
    *, python_version: tuple[int, int, int], node_version: str
) -> dict[str, str]:
    if python_version[:2] != (3, 11):
        raise PreflightError(
            f"Python 3.11 is required; found {'.'.join(map(str, python_version))}"
        )
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", node_version.strip())
    if match is None or int(match.group(1)) not in {20, 22}:
        raise PreflightError(
            f"Node.js 20 or 22 LTS is required; found {node_version.strip()}"
        )
    return {
        "python": ".".join(map(str, python_version)),
        "node": ".".join(match.groups()),
    }


def redact_connection_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.password is None:
        return value
    username = parsed.username or ""
    host = parsed.hostname or ""
    port = f":{parsed.port}" if parsed.port is not None else ""
    netloc = f"{username}:***@{host}{port}"
    return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))


def check_redis(client, *, key: str = "stage41:preflight", ttl_seconds: int = 30) -> dict:
    value = "ok"
    try:
        ping = bool(client.ping())
        client.set(key, value, ex=ttl_seconds)
        stored = client.get(key)
        if isinstance(stored, bytes):
            stored = stored.decode("utf-8")
        ttl = client.ttl(key)
        return {
            "ping": ping,
            "read_write": stored == value,
            "ttl": 0 < int(ttl) <= ttl_seconds,
        }
    finally:
        client.delete(key)


def validate_runtime_control_snapshot(
    *,
    tables: list[str],
    indexes: list[str],
    foreign_keys: dict,
    expected_tables: list[str],
    ledger_latencies_ms: list[float],
) -> dict:
    if set(tables) != set(expected_tables):
        raise PreflightError("runtime control tables are incomplete")
    if len(indexes) < 8:
        raise PreflightError("runtime control indexes are incomplete")
    if len(foreign_keys) != 3 or any(
        value != ("session_id", "CASCADE")
        for value in foreign_keys.values()
    ):
        raise PreflightError("runtime control cascade foreign keys are invalid")
    if len(ledger_latencies_ms) != 20:
        raise PreflightError("agent ledger latency sample is incomplete")
    ordered = sorted(ledger_latencies_ms)
    p95 = ordered[ceil(len(ordered) * 0.95) - 1]
    if p95 > 50:
        raise PreflightError("agent ledger p95 exceeds 50 ms")
    return {
        "tables": len(tables),
        "indexes": len(indexes),
        "cascade_foreign_keys": len(foreign_keys),
        "ledger_insert_p95_ms": round(p95, 3),
    }


def check_postgres_runtime() -> dict:
    from app.services.agent_runtime import AgentRunRecord
    from app.services.postgres_session import (
        PostgresInterviewSessionStore,
    )

    store = PostgresInterviewSessionStore(
        dsn=get_postgres_dsn(),
        table_prefix=get_runtime_table_prefix(),
    )
    control = store._runtime_control
    correlation_id = f"preflight-{uuid4().hex}"
    latencies = []
    try:
        for index in range(20):
            record = AgentRunRecord(
                run_id=f"agent-preflight-{uuid4().hex}",
                correlation_id=correlation_id,
                agent="knowledge",
                operation="preflight",
                phase="prep",
                status="completed",
                started_at="2026-07-17T00:00:00Z",
                finished_at="2026-07-17T00:00:00Z",
                latency_ms=0,
                output_type="NoneType",
            )
            started = perf_counter()
            control.record_agent_run(record)
            latencies.append((perf_counter() - started) * 1000)
        return validate_runtime_control_snapshot(
            tables=control.list_control_tables(),
            indexes=control.list_control_indexes(),
            foreign_keys=control.list_foreign_keys(),
            expected_tables=[
                control.outbox_table,
                control.receipts_table,
                control.agent_runs_table,
            ],
            ledger_latencies_ms=latencies,
        )
    finally:
        control.delete_agent_runs_by_correlation(correlation_id)


def _node_version() -> str:
    try:
        return subprocess.check_output(
            ["node", "--version"], text=True, stderr=subprocess.STDOUT
        ).strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise PreflightError("Node.js 20 is required but node is unavailable") from exc


def _redis_client(url: str):
    try:
        from redis import Redis
    except ImportError as exc:
        raise PreflightError("redis package is required for the Celery profile") from exc
    return Redis.from_url(url, socket_connect_timeout=3, socket_timeout=3)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Local V1 prerequisites")
    parser.add_argument(
        "--profile",
        choices=("core", "celery", "runtime"),
        default="core",
    )
    args = parser.parse_args()
    try:
        result = validate_runtime_versions(
            python_version=sys.version_info[:3], node_version=_node_version()
        )
        result["profile"] = args.profile
        result["langgraph"] = validate_langgraph_configuration(
            runtime_store=os.getenv("INTERVIEW_RUNTIME_STORE", "postgres"),
            runtime_enabled=get_interview_langgraph_runtime_enabled(),
            rollout_percent=get_interview_langgraph_rollout_percent(),
            strict_msgpack=os.getenv("LANGGRAPH_STRICT_MSGPACK", "true"),
            retention_hours=get_interview_chunk_retention_hours(),
        )
        result["review_langgraph"] = validate_langgraph_configuration(
            runtime_store=os.getenv("INTERVIEW_RUNTIME_STORE", "postgres"),
            runtime_enabled=get_report_langgraph_runtime_enabled(),
            rollout_percent=get_report_langgraph_rollout_percent(),
            strict_msgpack=os.getenv("LANGGRAPH_STRICT_MSGPACK", "true"),
            retention_hours=1,
        )
        if should_check_langgraph_postgres(
            runtime_store=result["langgraph"]["runtime_store"],
            interview_runtime_enabled=result["langgraph"][
                "runtime_enabled"
            ],
            review_runtime_enabled=result["review_langgraph"][
                "runtime_enabled"
            ],
            profile=args.profile,
        ):
            result["langgraph"]["postgres"] = check_langgraph_runtime()
        if (
            not result["langgraph"]["runtime_enabled"]
            and result["langgraph"]["rollout_percent"] == 0
            and result["review_langgraph"]["runtime_enabled"]
        ):
            result["warnings"] = [
                "interview_resume_capability_unverified"
            ]
        if args.profile == "celery":
            url = get_redis_url()
            result["redis_url"] = redact_connection_url(url)
            result["redis"] = check_redis(_redis_client(url))
        if args.profile == "runtime":
            result["runtime_control"] = check_postgres_runtime()
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
