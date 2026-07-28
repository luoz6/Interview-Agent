from __future__ import annotations

import argparse
from contextlib import contextmanager
from itertools import count
import json
from pathlib import Path

from app.services.config import (
    get_postgres_capacity_settings,
    get_postgres_dsn,
    get_postgres_pool_settings,
    get_pgvector_table,
    get_runtime_table_prefix,
)
from app.services.postgres_capacity import (
    build_blocked_config_artifact,
    build_capacity_artifact,
    query_postgres_server_capacity,
    run_deterministic_multi_domain_overlap,
)
from app.services.postgres_connection_domains import PostgresConnectionDomains
from app.services.postgres_connections import DirectPsycopg2ConnectionProvider
from app.services.postgres_connections import PostgresSchemaNotReady
from app.services.postgres_schema import validate_relations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Stage 48 PostgreSQL capacity evidence")
    parser.add_argument(
        "--output",
        default="reports/stage48-acceptance/postgres-capacity-v1.json",
    )
    args = parser.parse_args(argv)
    try:
        pools = get_postgres_pool_settings()
        capacity = get_postgres_capacity_settings()
        dsn = get_postgres_dsn()
        prefix = get_runtime_table_prefix()
        vector = get_pgvector_table()
    except (TypeError, ValueError):
        artifact = build_blocked_config_artifact()
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(artifact, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print("status=BLOCKED_CONFIG")
        print(f"artifact={output.as_posix()}")
        return 1
    direct = DirectPsycopg2ConnectionProvider(dsn)
    server = query_postgres_server_capacity(direct)
    try:
        validate_relations(
            direct,
            (
                f"{prefix}_sessions",
                f"{prefix}_runtime_outbox",
                f"{prefix}_generation_attempts",
                f"{prefix}_report_jobs",
                f"{prefix}_review_effects",
                f"{prefix}_runtime_signal_buckets",
                f"{prefix}_schema_migrations",
                f"{vector}_versions",
                f"{vector}_releases",
            ),
        )
        schema_ready = True
    except PostgresSchemaNotReady:
        schema_ready = False
    domains = PostgresConnectionDomains(dsn=dsn, settings=pools)
    domains.open()
    try:
        load_errors = []
        observed_checkpointer_peak = 0
        observed_application_peak = 0
        observed_advisory_locks = 0
        simultaneous_domains_verified = False
        if schema_ready:
            domains.checkpointer.start()
            lock_keys = count(1)

            @contextmanager
            def acquire_advisory_lock():
                key = 4_800_000 + next(lock_keys)
                with domains.advisory_lock.exclusive_connection(
                    autocommit=True
                ) as connection:
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT pg_try_advisory_lock(%s)", (key,))
                        if not cursor.fetchone()[0]:
                            raise RuntimeError("capacity advisory lock was busy")
                    try:
                        yield connection
                    finally:
                        with connection.cursor() as cursor:
                            cursor.execute("SELECT pg_advisory_unlock(%s)", (key,))
                            if not cursor.fetchone()[0]:
                                connection.close()
                                raise RuntimeError(
                                    "capacity advisory lock release failed"
                                )

            def observe_checkpointer():
                nonlocal observed_checkpointer_peak
                nonlocal observed_application_peak
                nonlocal observed_advisory_locks
                nonlocal simultaneous_domains_verified
                with direct.connection() as connection:
                    with connection.cursor() as cursor:
                        cursor.execute(
                            "SELECT COUNT(*) FROM pg_stat_activity "
                            "WHERE application_name = 'interview_checkpointer'"
                        )
                        observed_checkpointer_peak = int(cursor.fetchone()[0])
                        cursor.execute(
                            "SELECT COUNT(*) FROM pg_stat_activity "
                            "WHERE application_name LIKE 'interview_%'"
                        )
                        current_application = int(cursor.fetchone()[0])
                        observed_application_peak = max(
                            0,
                            current_application
                            - server.stage48_application_connection_count,
                        )
                        cursor.execute(
                            "SELECT COUNT(*) FROM pg_locks AS locks "
                            "JOIN pg_stat_activity AS activity "
                            "ON activity.pid = locks.pid "
                            "WHERE locks.locktype = 'advisory' "
                            "AND locks.granted "
                            "AND activity.application_name = 'interview_lock'"
                        )
                        observed_advisory_locks = int(cursor.fetchone()[0])
                        simultaneous_domains_verified = True

            load_errors.extend(
                run_deterministic_multi_domain_overlap(
                    (
                        (
                            "business",
                            domains.business.connection,
                            pools.business_max_size,
                        ),
                        (
                            "telemetry",
                            domains.telemetry.connection,
                            pools.telemetry_max_size,
                        ),
                        (
                            "advisory_lock",
                            acquire_advisory_lock,
                            pools.lock_max_size,
                        ),
                        (
                            "checkpointer",
                            lambda: domains.checkpointer.pool.connection(
                                timeout=(
                                    pools.checkpointer_acquire_timeout_seconds
                                )
                            ),
                            pools.checkpointer_max_size,
                        ),
                    ),
                    observer=observe_checkpointer,
                )
            )
        snapshots = domains.snapshot()
        domain_values = {
            name: {
                "max_size": snapshot.max_size,
                "peak_leased": snapshot.peak_leased,
                "acquire_timeout_count": snapshot.acquire_timeout_count,
                "discard_count": snapshot.discard_count,
                "p95_wait_ms": snapshot.p95_wait_ms,
            }
            for name, snapshot in {
                "business": snapshots.business,
                "telemetry": snapshots.telemetry,
                "advisory_lock": snapshots.advisory_lock,
            }.items()
        }
        artifact = build_capacity_artifact(
            pools=pools,
            capacity=capacity,
            server=server,
            domain_snapshots=domain_values,
            schema_ready=schema_ready,
            load_passed=schema_ready and not load_errors,
            observed_checkpointer_peak=observed_checkpointer_peak,
            observed_application_peak=observed_application_peak,
            expected_application_peak=(
                pools.business_max_size
                + pools.telemetry_max_size
                + pools.lock_max_size
                + pools.checkpointer_max_size
            ),
            observed_advisory_locks=observed_advisory_locks,
            expected_advisory_locks=pools.lock_max_size,
            simultaneous_domains_verified=simultaneous_domains_verified,
        )
    finally:
        domains.close()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(artifact, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"status={artifact['status']}")
    print(f"artifact={output.as_posix()}")
    return 0 if artifact["status"] == "ELIGIBLE_FOR_CAPACITY_CANARY" else 1


if __name__ == "__main__":
    raise SystemExit(main())
