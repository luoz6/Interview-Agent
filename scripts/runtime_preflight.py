import argparse
import json
import re
import subprocess
import sys
from math import ceil
from time import perf_counter
from uuid import uuid4
from urllib.parse import urlsplit, urlunsplit

from app.services.config import (
    get_postgres_dsn,
    get_redis_url,
    get_runtime_table_prefix,
)


class PreflightError(RuntimeError):
    pass


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
