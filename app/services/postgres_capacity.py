from __future__ import annotations

from dataclasses import asdict, dataclass
from math import floor
from threading import Barrier, Event, Thread
from typing import Any, Literal

from app.services.config import PostgresCapacitySettings, PostgresPoolSettings
from app.services.postgres_connections import ConnectionProvider


CapacityStatus = Literal[
    "BLOCKED_CONFIG",
    "BLOCKED_SCHEMA",
    "BLOCKED_BUDGET",
    "FAILED_LOAD",
    "ELIGIBLE_FOR_CAPACITY_CANARY",
]


def build_blocked_config_artifact() -> dict[str, Any]:
    """Emit a stable diagnostic without serializing the invalid value."""

    return {
        "schema_version": "postgres-capacity-v1",
        "status": "BLOCKED_CONFIG",
        "config_error_code": "invalid_postgres_capacity_config",
        "evidence_level": "repository",
        "production_observation": "NOT_RUN",
    }


@dataclass(frozen=True)
class PostgresServerCapacity:
    max_connections: int
    superuser_reserved_connections: int
    current_connection_count: int
    stage48_application_connection_count: int

    @property
    def server_available(self) -> int:
        return max(0, self.max_connections - self.superuser_reserved_connections)


@dataclass(frozen=True)
class PostgresRoleBudget:
    api: int
    celery: int
    outbox: int
    configured_total: int


def calculate_role_budgets(
    pools: PostgresPoolSettings,
    capacity: PostgresCapacitySettings,
) -> PostgresRoleBudget:
    checkpointer = pools.checkpointer_max_size + pools.checkpointer_overhead
    all_domains = (
        pools.business_max_size
        + pools.telemetry_max_size
        + pools.lock_max_size
        + checkpointer
    )
    # The current outbox sink can invoke both durable graphs, so it needs the
    # same four-domain ceiling. Deployments with a publish-only outbox role may
    # lower this only through a separately declared role profile.
    api = all_domains
    celery = all_domains
    outbox = all_domains
    configured_total = (
        capacity.expected_api_processes * api
        + capacity.expected_celery_processes * celery
        + capacity.expected_outbox_processes * outbox
    )
    return PostgresRoleBudget(
        api=api,
        celery=celery,
        outbox=outbox,
        configured_total=configured_total,
    )


def query_postgres_server_capacity(
    provider: ConnectionProvider,
) -> PostgresServerCapacity:
    with provider.connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SHOW max_connections")
            maximum = int(_first_value(cursor.fetchone()))
            cursor.execute("SHOW superuser_reserved_connections")
            reserved = int(_first_value(cursor.fetchone()))
            cursor.execute("SELECT COUNT(*) FROM pg_stat_activity")
            current = int(_first_value(cursor.fetchone()))
            cursor.execute(
                "SELECT COUNT(*) FROM pg_stat_activity "
                "WHERE application_name LIKE 'interview_%'"
            )
            stage48 = int(_first_value(cursor.fetchone()))
    return PostgresServerCapacity(
        max_connections=maximum,
        superuser_reserved_connections=reserved,
        current_connection_count=current,
        stage48_application_connection_count=stage48,
    )


def build_capacity_artifact(
    *,
    pools: PostgresPoolSettings,
    capacity: PostgresCapacitySettings,
    server: PostgresServerCapacity,
    domain_snapshots: dict[str, dict[str, Any]],
    schema_ready: bool,
    load_passed: bool,
    observed_checkpointer_peak: int,
    observed_application_peak: int = 0,
    expected_application_peak: int = 0,
    observed_advisory_locks: int = 0,
    expected_advisory_locks: int = 0,
    simultaneous_domains_verified: bool = False,
    privacy_violations: int = 0,
) -> dict[str, Any]:
    budgets = calculate_role_budgets(pools, capacity)
    available = max(
        0,
        server.server_available - capacity.external_connection_reserve,
    )
    allowed = floor(available * capacity.max_utilization)
    if not schema_ready:
        status: CapacityStatus = "BLOCKED_SCHEMA"
    elif budgets.configured_total > allowed:
        status = "BLOCKED_BUDGET"
    elif (
        not load_passed
        or privacy_violations
        or observed_checkpointer_peak
        > pools.checkpointer_max_size + pools.checkpointer_overhead
        or not simultaneous_domains_verified
        or observed_application_peak < expected_application_peak
        or observed_application_peak
        > expected_application_peak + pools.checkpointer_overhead
        or observed_advisory_locks < expected_advisory_locks
        or any(
            int(snapshot.get("peak_leased", 0))
            > int(snapshot.get("max_size", 0))
            or int(snapshot.get("acquire_timeout_count", 0)) > 0
            for snapshot in domain_snapshots.values()
        )
    ):
        status = "FAILED_LOAD"
    else:
        status = "ELIGIBLE_FOR_CAPACITY_CANARY"
    return {
        "schema_version": "postgres-capacity-v1",
        "status": status,
        "process_budget": {
            **asdict(budgets),
            "available": available,
            "allowed_at_utilization": allowed,
            "max_utilization": capacity.max_utilization,
            "external_connection_reserve": capacity.external_connection_reserve,
        },
        "server": {
            "max_connections": server.max_connections,
            "superuser_reserved_connections": (
                server.superuser_reserved_connections
            ),
            "current_connection_count": server.current_connection_count,
            "stage48_application_connection_count": (
                server.stage48_application_connection_count
            ),
        },
        "checkpointer": {
            "max_size": pools.checkpointer_max_size,
            "configured_overhead": pools.checkpointer_overhead,
            "observed_peak": observed_checkpointer_peak,
            "within_pool_max": (
                observed_checkpointer_peak <= pools.checkpointer_max_size
            ),
            "within_budgeted_max": (
                observed_checkpointer_peak
                <= pools.checkpointer_max_size + pools.checkpointer_overhead
            ),
        },
        "domains": domain_snapshots,
        "simultaneous_capacity": {
            "verified": simultaneous_domains_verified,
            "expected_application_peak": expected_application_peak,
            "observed_application_peak": observed_application_peak,
            "expected_advisory_locks": expected_advisory_locks,
            "observed_advisory_locks": observed_advisory_locks,
        },
        "schema_ready": schema_ready,
        "load_passed": load_passed,
        "privacy_violations": privacy_violations,
        "evidence_level": "repository",
        "production_observation": "NOT_RUN",
    }


def run_deterministic_connection_overlap(
    acquire,
    *,
    lease_count: int,
    observer=None,
    timeout_seconds: float = 10.0,
) -> list[Exception]:
    """Hold exactly lease_count connections concurrently using a barrier."""

    if lease_count < 1:
        raise ValueError("lease_count must be positive")
    barrier = Barrier(lease_count + 1)
    release = Event()
    errors: list[Exception] = []

    def worker() -> None:
        try:
            with acquire():
                barrier.wait(timeout=timeout_seconds)
                if not release.wait(timeout_seconds):
                    raise TimeoutError("capacity overlap release timed out")
        except Exception as exc:
            errors.append(exc)
            try:
                barrier.abort()
            except Exception:
                pass

    threads = [Thread(target=worker, daemon=True) for _ in range(lease_count)]
    for thread in threads:
        thread.start()
    try:
        barrier.wait(timeout=timeout_seconds)
        if observer is not None:
            observer()
    except Exception as exc:
        errors.append(exc)
    finally:
        release.set()
        for thread in threads:
            thread.join(timeout_seconds)
            if thread.is_alive():
                errors.append(TimeoutError("capacity overlap worker did not stop"))
    return errors


def run_deterministic_multi_domain_overlap(
    acquisitions,
    *,
    observer=None,
    timeout_seconds: float = 15.0,
) -> list[Exception]:
    """Hold every configured domain lease at once before observing capacity."""

    expanded = [
        (domain, acquire)
        for domain, acquire, lease_count in acquisitions
        for _ in range(lease_count)
    ]
    if not expanded:
        raise ValueError("at least one domain lease is required")
    barrier = Barrier(len(expanded) + 1)
    release = Event()
    errors: list[Exception] = []

    def worker(acquire) -> None:
        try:
            with acquire():
                barrier.wait(timeout=timeout_seconds)
                if not release.wait(timeout_seconds):
                    raise TimeoutError("multi-domain capacity release timed out")
        except Exception as exc:
            errors.append(exc)
            try:
                barrier.abort()
            except Exception:
                pass

    threads = [
        Thread(target=worker, args=(acquire,), daemon=True, name=f"capacity-{domain}")
        for domain, acquire in expanded
    ]
    for thread in threads:
        thread.start()
    try:
        barrier.wait(timeout=timeout_seconds)
        if observer is not None:
            observer()
    except Exception as exc:
        errors.append(exc)
    finally:
        release.set()
        for thread in threads:
            thread.join(timeout_seconds)
            if thread.is_alive():
                errors.append(
                    TimeoutError("multi-domain capacity worker did not stop")
                )
    return errors


def _first_value(row: Any) -> Any:
    if isinstance(row, dict):
        return next(iter(row.values()))
    return row[0]
