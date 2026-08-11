from __future__ import annotations

import argparse
from contextlib import contextmanager
from itertools import count
import os
from pathlib import Path

from app.ports.postgres_scope import PostgresScopeError
from app.runtime.config.compatibility import (
    PostgresCapacitySettings,
    PostgresPoolSettings,
    get_postgres_capacity_settings,
    get_postgres_pool_settings,
)
from app.services.postgres_capacity import (
    PostgresServerCapacity,
    build_capacity_evidence_payload,
    query_postgres_server_capacity,
    run_deterministic_multi_domain_overlap,
)
from app.services.postgres_connection_domains import PostgresConnectionDomains
from app.services.postgres_connections import (
    DirectPsycopg2ConnectionProvider,
    PostgresSchemaNotReady,
)
from contracts.evidence import (
    AtomicEvidenceWriter,
    EvidenceIssuer,
    EvidenceRegistry,
    EvidenceVerifier,
)
from contracts.evidence.rendering import render_gate_lines
from contracts.policies import CapacityEvidencePolicy
from scripts.postgres_acceptance_support import (
    AcceptanceConfigurationError,
    approved_postgres_scope,
    load_receipt_signer,
    require_environment_value,
)


TOOL_VERSION = "2.0.0"
DEFAULT_OUTPUT = "reports/stage48-acceptance/postgres-capacity-v2.json"


def _collect_capacity_evidence(
    *,
    dsn: str,
    pools: PostgresPoolSettings,
    capacity: PostgresCapacitySettings,
) -> tuple[object, PostgresServerCapacity]:
    direct = DirectPsycopg2ConnectionProvider(
        dsn,
        connect_kwargs={"connect_timeout": pools.connect_timeout_seconds},
    )
    server = query_postgres_server_capacity(direct)
    domains = PostgresConnectionDomains(dsn=dsn, settings=pools)
    domains.open()
    try:
        load_errors: list[Exception] = []
        observed_checkpointer_peak = 0
        observed_application_peak = 0
        observed_advisory_locks = 0
        simultaneous_domains_verified = False
        try:
            domains.checkpointer.start()
            schema_ready = True
        except PostgresSchemaNotReady as exc:
            schema_ready = False
            load_errors.append(exc)

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

        expected_application_peak = (
            pools.business_max_size
            + pools.telemetry_max_size
            + pools.lock_max_size
            + pools.checkpointer_max_size
        )
        if schema_ready:
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
                                timeout=pools.checkpointer_acquire_timeout_seconds
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
                "p95_wait_ms": float(snapshot.p95_wait_ms),
            }
            for name, snapshot in {
                "business": snapshots.business,
                "telemetry": snapshots.telemetry,
                "advisory_lock": snapshots.advisory_lock,
            }.items()
        }
        payload = build_capacity_evidence_payload(
            pools=pools,
            capacity=capacity,
            server=server,
            domain_snapshots=domain_values,
            schema_ready=schema_ready,
            load_errors=load_errors,
            observed_checkpointer_peak=observed_checkpointer_peak,
            observed_application_peak=observed_application_peak,
            expected_application_peak=expected_application_peak,
            observed_advisory_locks=observed_advisory_locks,
            expected_advisory_locks=pools.lock_max_size,
            simultaneous_domains_verified=simultaneous_domains_verified,
            synthetic=True,
        )
        return payload, server
    finally:
        domains.close()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PostgreSQL capacity evidence")
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--revision", default=None)
    parser.add_argument("--scope-prefix", default=None)
    parser.add_argument("--production-scope", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.execute:
        print("mode=DRY_RUN")
        print("status=NOT_RUN")
        print("gate=CAPACITY_EXECUTION_NOT_REQUESTED")
        return 0

    try:
        pools = get_postgres_pool_settings()
        capacity = get_postgres_capacity_settings()
        dsn = require_environment_value(os.environ, "POSTGRES_DSN")
        revision = args.revision or require_environment_value(
            os.environ,
            "EVIDENCE_REVISION",
        )
        scope_prefix = args.scope_prefix or require_environment_value(
            os.environ,
            "POSTGRES_ACCEPTANCE_SCOPE_PREFIX",
        )
        signer = load_receipt_signer(os.environ)
    except (AcceptanceConfigurationError, TypeError, ValueError):
        print("status=BLOCKED")
        print("gate=ACCEPTANCE_CONFIGURATION_INVALID")
        return 1

    try:
        with approved_postgres_scope(
            dsn=dsn,
            scope_prefix=scope_prefix,
            environ=os.environ,
        ):
            payload, _server = _collect_capacity_evidence(
                dsn=dsn,
                pools=pools,
                capacity=capacity,
            )
    except (AcceptanceConfigurationError, PostgresScopeError) as exc:
        print("status=BLOCKED")
        print(f"gate={exc.code}")
        return 1

    policy = CapacityEvidencePolicy(
        minimum_samples=1,
        minimum_headroom_percent=0.0,
    )
    result = policy.evaluate(payload, production_scope=args.production_scope)
    evidence_scope = (
        "capacity.production" if args.production_scope else "capacity.controlled"
    )
    issuer = EvidenceIssuer(signer=signer)
    bundle = issuer.issue(
        payload_type="capacity-evidence",
        payload=payload,
        policy_result=result,
        producer="scripts.postgres-capacity-acceptance",
        tool_version=TOOL_VERSION,
        revision=revision,
        scope=evidence_scope,
    )
    verifier = EvidenceVerifier(
        registry=EvidenceRegistry.default(),
        receipt_signer=signer,
    )

    def verify_written(value: dict) -> None:
        verifier.verify(
            value,
            expected_revision=revision,
            expected_scope=evidence_scope,
        )

    output = Path(args.output)
    AtomicEvidenceWriter(post_write_verifier=verify_written).write(output, bundle)
    for line in render_gate_lines(bundle):
        print(line)
    legacy_status = (
        "ELIGIBLE_FOR_CAPACITY_CANARY"
        if result.verification_status.value == "PASS"
        else "BLOCKED_CAPACITY_CANARY"
    )
    print(f"legacy_status={legacy_status}")
    print(f"artifact={output.as_posix()}")
    return 0 if result.verification_status.value == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
