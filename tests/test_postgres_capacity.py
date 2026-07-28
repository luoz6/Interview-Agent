from app.services.config import PostgresCapacitySettings, PostgresPoolSettings
from app.services.postgres_capacity import (
    PostgresServerCapacity,
    build_blocked_config_artifact,
    build_capacity_artifact,
    calculate_role_budgets,
    run_deterministic_connection_overlap,
    run_deterministic_multi_domain_overlap,
)
from contextlib import contextmanager
from threading import Lock


def pools() -> PostgresPoolSettings:
    return PostgresPoolSettings(
        business_min_size=1,
        business_max_size=12,
        business_acquire_timeout_seconds=2,
        telemetry_min_size=1,
        telemetry_max_size=4,
        telemetry_acquire_timeout_seconds=1,
        lock_min_size=1,
        lock_max_size=4,
        lock_acquire_timeout_seconds=2,
        checkpointer_min_size=1,
        checkpointer_max_size=2,
        checkpointer_acquire_timeout_seconds=2,
        checkpointer_overhead=1,
        connect_timeout_seconds=3,
        drain_timeout_seconds=10,
        max_lifetime_seconds=1800,
        max_idle_seconds=300,
    )


def capacity() -> PostgresCapacitySettings:
    return PostgresCapacitySettings(
        expected_api_processes=1,
        expected_celery_processes=1,
        expected_outbox_processes=1,
        external_connection_reserve=10,
        max_utilization=0.8,
    )


def healthy_domains():
    return {
        "business": {"max_size": 12, "peak_leased": 12, "acquire_timeout_count": 0},
        "telemetry": {"max_size": 4, "peak_leased": 4, "acquire_timeout_count": 0},
        "advisory_lock": {"max_size": 4, "peak_leased": 4, "acquire_timeout_count": 0},
    }


def test_default_role_budget_includes_checkpointer_overhead_for_every_role():
    result = calculate_role_budgets(pools(), capacity())

    assert result.api == 23
    assert result.celery == 23
    assert result.outbox == 23
    assert result.configured_total == 69


def test_blocked_config_artifact_is_reachable_and_privacy_safe():
    artifact = build_blocked_config_artifact()

    assert artifact == {
        "schema_version": "postgres-capacity-v1",
        "status": "BLOCKED_CONFIG",
        "config_error_code": "invalid_postgres_capacity_config",
        "evidence_level": "repository",
        "production_observation": "NOT_RUN",
    }


def test_capacity_artifact_is_eligible_at_exact_allowed_boundary():
    artifact = build_capacity_artifact(
        pools=pools(),
        capacity=capacity(),
        server=PostgresServerCapacity(100, 3, 5, 0),
        domain_snapshots=healthy_domains(),
        schema_ready=True,
        load_passed=True,
        observed_checkpointer_peak=2,
        observed_application_peak=22,
        expected_application_peak=22,
        observed_advisory_locks=4,
        expected_advisory_locks=4,
        simultaneous_domains_verified=True,
    )

    assert artifact["schema_version"] == "postgres-capacity-v1"
    assert artifact["process_budget"]["allowed_at_utilization"] == 69
    assert artifact["status"] == "ELIGIBLE_FOR_CAPACITY_CANARY"
    assert artifact["production_observation"] == "NOT_RUN"


def test_capacity_artifact_blocks_budget_before_load_claim():
    artifact = build_capacity_artifact(
        pools=pools(),
        capacity=capacity(),
        server=PostgresServerCapacity(80, 3, 5, 0),
        domain_snapshots=healthy_domains(),
        schema_ready=True,
        load_passed=True,
        observed_checkpointer_peak=2,
        observed_application_peak=22,
        expected_application_peak=22,
        observed_advisory_locks=4,
        expected_advisory_locks=4,
        simultaneous_domains_verified=True,
    )

    assert artifact["status"] == "BLOCKED_BUDGET"


def test_capacity_artifact_fails_peak_or_timeout_anomaly():
    domains = healthy_domains()
    domains["business"] = {
        "max_size": 12,
        "peak_leased": 13,
        "acquire_timeout_count": 1,
    }
    artifact = build_capacity_artifact(
        pools=pools(),
        capacity=capacity(),
        server=PostgresServerCapacity(200, 3, 5, 0),
        domain_snapshots=domains,
        schema_ready=True,
        load_passed=True,
        observed_checkpointer_peak=4,
        observed_application_peak=22,
        expected_application_peak=22,
        observed_advisory_locks=4,
        expected_advisory_locks=4,
        simultaneous_domains_verified=True,
    )

    assert artifact["status"] == "FAILED_LOAD"


def test_deterministic_overlap_proves_real_concurrent_leases():
    lock = Lock()
    active = 0
    peak = 0

    @contextmanager
    def acquire():
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        try:
            yield object()
        finally:
            with lock:
                active -= 1

    errors = run_deterministic_connection_overlap(acquire, lease_count=4)

    assert errors == []
    assert peak == 4
    assert active == 0


def test_multi_domain_overlap_holds_every_domain_at_the_same_time():
    lock = Lock()
    active = {"business": 0, "telemetry": 0, "lock": 0, "checkpointer": 0}
    observed = []

    def factory(domain):
        @contextmanager
        def acquire():
            with lock:
                active[domain] += 1
            try:
                yield object()
            finally:
                with lock:
                    active[domain] -= 1

        return acquire

    errors = run_deterministic_multi_domain_overlap(
        tuple((name, factory(name), count) for name, count in {
            "business": 3,
            "telemetry": 2,
            "lock": 2,
            "checkpointer": 1,
        }.items()),
        observer=lambda: observed.append(dict(active)),
    )

    assert errors == []
    assert observed == [
        {"business": 3, "telemetry": 2, "lock": 2, "checkpointer": 1}
    ]
    assert active == {"business": 0, "telemetry": 0, "lock": 0, "checkpointer": 0}
