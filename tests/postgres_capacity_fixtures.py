from app.runtime.config.compatibility import PostgresCapacitySettings, PostgresPoolSettings


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
        "business": {
            "max_size": 12,
            "peak_leased": 12,
            "acquire_timeout_count": 0,
            "discard_count": 0,
            "p95_wait_ms": 1.0,
        },
        "telemetry": {
            "max_size": 4,
            "peak_leased": 4,
            "acquire_timeout_count": 0,
            "discard_count": 0,
            "p95_wait_ms": 1.0,
        },
        "advisory_lock": {
            "max_size": 4,
            "peak_leased": 4,
            "acquire_timeout_count": 0,
            "discard_count": 0,
            "p95_wait_ms": 1.0,
        },
    }
