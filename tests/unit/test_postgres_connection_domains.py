from app.runtime.config.compatibility import PostgresPoolSettings, get_postgres_pool_settings
import pytest

from app.services.postgres_connection_domains import PostgresConnectionDomains
from app.services.postgres_connections import PostgresPoolDrainTimeout


class FakeConnection:
    autocommit = False
    closed = False

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        self.closed = True


class FakePsycopg2Pool:
    instances = []

    def __init__(self, minconn, maxconn, dsn, **kwargs):
        self.closed = False
        self.connections = []
        self.kwargs = kwargs
        self.__class__.instances.append(self)

    def getconn(self):
        connection = FakeConnection()
        self.connections.append(connection)
        return connection

    def putconn(self, connection, close=False):
        if close:
            connection.close()

    def closeall(self):
        self.closed = True


class FakeCheckpointerPool:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


def settings() -> PostgresPoolSettings:
    return PostgresPoolSettings(
        business_min_size=0,
        business_max_size=2,
        business_acquire_timeout_seconds=0.1,
        telemetry_min_size=0,
        telemetry_max_size=1,
        telemetry_acquire_timeout_seconds=0.1,
        lock_min_size=0,
        lock_max_size=1,
        lock_acquire_timeout_seconds=0.1,
        checkpointer_min_size=0,
        checkpointer_max_size=1,
        checkpointer_acquire_timeout_seconds=0.1,
        checkpointer_overhead=1,
        connect_timeout_seconds=3,
        drain_timeout_seconds=0.1,
        max_lifetime_seconds=1800,
        max_idle_seconds=300,
    )


def test_domains_open_three_independent_psycopg2_pools_and_close_in_owner():
    FakePsycopg2Pool.instances = []
    domains = PostgresConnectionDomains(
        dsn="safe-dsn",
        settings=settings(),
        psycopg2_pool_factory=FakePsycopg2Pool,
        checkpointer_pool_factory=FakeCheckpointerPool,
        checkpointer_schema_validator=lambda pool: None,
    )

    domains.open()

    assert domains.state == "open"
    assert len(FakePsycopg2Pool.instances) == 3
    assert domains.business is not domains.telemetry
    assert domains.telemetry is not domains.advisory_lock
    assert domains.snapshot().checkpointer_state == "new"
    assert all(
        pool.kwargs["connect_timeout"] == settings().connect_timeout_seconds
        for pool in FakePsycopg2Pool.instances
    )

    domains.close()
    assert domains.state == "closed"
    assert all(pool.closed for pool in FakePsycopg2Pool.instances)


def test_pool_configuration_defaults_are_conservative(monkeypatch):
    for name in (
        "POSTGRES_BUSINESS_POOL_MIN_SIZE",
        "POSTGRES_BUSINESS_POOL_MAX_SIZE",
        "POSTGRES_TELEMETRY_POOL_MIN_SIZE",
        "POSTGRES_TELEMETRY_POOL_MAX_SIZE",
        "POSTGRES_LOCK_POOL_MIN_SIZE",
        "POSTGRES_LOCK_POOL_MAX_SIZE",
        "POSTGRES_CHECKPOINTER_POOL_MIN_SIZE",
        "POSTGRES_CHECKPOINTER_POOL_MAX_SIZE",
        "POSTGRES_CHECKPOINTER_POOL_OVERHEAD",
    ):
        monkeypatch.delenv(name, raising=False)

    resolved = get_postgres_pool_settings()

    assert (resolved.business_min_size, resolved.business_max_size) == (1, 12)
    assert (resolved.telemetry_min_size, resolved.telemetry_max_size) == (1, 4)
    assert (resolved.lock_min_size, resolved.lock_max_size) == (1, 4)
    assert (resolved.checkpointer_min_size, resolved.checkpointer_max_size) == (1, 2)
    assert resolved.checkpointer_overhead == 1


def test_pool_configuration_rejects_min_above_max(monkeypatch):
    monkeypatch.setenv("POSTGRES_BUSINESS_POOL_MIN_SIZE", "3")
    monkeypatch.setenv("POSTGRES_BUSINESS_POOL_MAX_SIZE", "2")

    try:
        get_postgres_pool_settings()
    except ValueError as exc:
        assert "BUSINESS" in str(exc)
    else:
        raise AssertionError("invalid pool bounds must fail")


def test_domain_close_can_retry_after_a_child_pool_drain_timeout():
    FakePsycopg2Pool.instances = []
    short = settings()
    short = PostgresPoolSettings(
        **{
            **short.__dict__,
            "drain_timeout_seconds": 0.001,
        }
    )
    domains = PostgresConnectionDomains(
        dsn="safe-dsn",
        settings=short,
        psycopg2_pool_factory=FakePsycopg2Pool,
        checkpointer_pool_factory=FakeCheckpointerPool,
        checkpointer_schema_validator=lambda pool: None,
    )
    domains.open()
    lease = domains.business.connection()
    lease.__enter__()

    with pytest.raises(PostgresPoolDrainTimeout):
        domains.close()

    assert domains.state == "closing"
    assert domains.business.state == "closing"
    assert FakePsycopg2Pool.instances[0].closed is False

    lease.__exit__(None, None, None)
    domains.close()

    assert domains.state == "closed"
    assert domains.business.state == "closed"
    assert FakePsycopg2Pool.instances[0].closed is True
