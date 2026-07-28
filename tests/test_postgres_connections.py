from __future__ import annotations

from threading import Event, Thread

import pytest

from app.services.postgres_connections import (
    ConnectionProvider,
    DirectPsycopg2ConnectionProvider,
    ExclusiveConnectionProvider,
    PooledPsycopg2ConnectionProvider,
    PostgresConnectionDiscarded,
    PostgresPoolClosed,
    PostgresPoolDrainTimeout,
    PostgresPoolExhausted,
)
from app.services.runtime_work import classify_runtime_failure


class FakeConnection:
    def __init__(self):
        self.closed = False
        self.autocommit = False
        self.commits = 0
        self.rollbacks = 0

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


class FakeThreadedPool:
    def __init__(self, minconn, maxconn, dsn, **kwargs):
        self.minconn = minconn
        self.maxconn = maxconn
        self.dsn = dsn
        self.kwargs = kwargs
        self.available = []
        self.created = []
        self.returned = []
        self.closed = False

    def getconn(self):
        if self.available:
            return self.available.pop()
        connection = FakeConnection()
        self.created.append(connection)
        return connection

    def putconn(self, connection, close=False):
        self.returned.append((connection, close))
        if close:
            connection.close()
        else:
            self.available.append(connection)

    def closeall(self):
        self.closed = True
        for connection in self.created:
            connection.close()


def make_provider(**overrides):
    pools = []

    def factory(*args, **kwargs):
        pool = FakeThreadedPool(*args, **kwargs)
        pools.append(pool)
        return pool

    options = {
        "domain": "business",
        "min_size": 0,
        "max_size": 1,
        "acquire_timeout": 0.05,
        "drain_timeout": 0.05,
        "pool_factory": factory,
    }
    options.update(overrides)
    provider = PooledPsycopg2ConnectionProvider("safe-dsn", **options)
    provider.open()
    return provider, pools[0]


def test_pool_wires_connect_timeout_into_physical_connection_factory():
    provider, pool = make_provider(connect_timeout=7)

    assert pool.kwargs["connect_timeout"] == 7
    provider.close()


def test_checkout_recycles_connection_after_max_idle():
    clock = [0.0]
    provider, pool = make_provider(
        max_idle=5.0,
        max_lifetime=100.0,
        monotonic_fn=lambda: clock[0],
    )

    with provider.connection() as first:
        pass
    clock[0] = 6.0
    with provider.connection() as second:
        pass

    assert first is not second
    assert first.closed is True
    assert any(connection is first and close for connection, close in pool.returned)
    assert provider.snapshot().discard_count == 1
    provider.close()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"min_size": -1},
        {"min_size": 2, "max_size": 1},
        {"max_size": 0},
        {"acquire_timeout": 0},
        {"drain_timeout": 0},
    ],
)
def test_pool_validates_bounds(kwargs):
    with pytest.raises(ValueError):
        PooledPsycopg2ConnectionProvider("dsn", domain="business", **kwargs)


def test_protocols_accept_direct_and_pooled_providers():
    direct = DirectPsycopg2ConnectionProvider("dsn", connect=lambda dsn: FakeConnection())
    pooled, _ = make_provider()

    assert isinstance(direct, ConnectionProvider)
    assert isinstance(direct, ExclusiveConnectionProvider)
    assert isinstance(pooled, ConnectionProvider)
    assert isinstance(pooled, ExclusiveConnectionProvider)
    pooled.close()


def test_checkout_commits_resets_and_returns_connection():
    provider, pool = make_provider()

    with provider.connection() as connection:
        assert provider.snapshot().leased == 1

    assert connection.commits == 1
    assert connection.rollbacks == 1
    assert pool.returned == [(connection, False)]
    assert provider.snapshot().acquire_count == 1
    provider.close()


def test_body_failure_rolls_back_and_preserves_business_exception():
    provider, pool = make_provider()

    with pytest.raises(LookupError, match="business"):
        with provider.connection() as connection:
            raise LookupError("business")

    assert connection.commits == 0
    assert connection.rollbacks >= 1
    assert pool.returned[-1] == (connection, False)
    provider.close()


def test_direct_connection_close_failure_does_not_replace_business_exception():
    class CloseFails(FakeConnection):
        def close(self):
            raise RuntimeError("close failed")

    provider = DirectPsycopg2ConnectionProvider(
        "dsn",
        connect=lambda dsn: CloseFails(),
    )

    with pytest.raises(LookupError, match="business"):
        with provider.connection():
            raise LookupError("business")


def test_waiter_succeeds_after_first_lease_returns():
    provider, _ = make_provider(acquire_timeout=0.5)
    entered = Event()
    release = Event()
    acquired = []

    def holder():
        with provider.connection():
            entered.set()
            release.wait(1)

    def waiter():
        with provider.connection():
            acquired.append(True)

    first = Thread(target=holder)
    second = Thread(target=waiter)
    first.start()
    assert entered.wait(1)
    second.start()
    release.set()
    first.join(1)
    second.join(1)

    assert acquired == [True]
    assert provider.snapshot().peak_leased == 1
    provider.close()


def test_acquire_timeout_has_stable_retryable_failure():
    provider, _ = make_provider()

    with provider.connection():
        with pytest.raises(PostgresPoolExhausted):
            with provider.connection():
                pass

    failure = classify_runtime_failure(PostgresPoolExhausted("private"))
    assert failure.code == "postgres_pool_exhausted"
    assert failure.retryable is True
    assert provider.snapshot().acquire_timeout_count == 1
    provider.close()


def test_closed_pool_rejects_new_work():
    provider, _ = make_provider()
    provider.close()

    with pytest.raises(PostgresPoolClosed):
        with provider.connection():
            pass


def test_close_is_bounded_while_a_lease_is_active():
    provider, _ = make_provider(drain_timeout=0.01)

    with provider.connection():
        with pytest.raises(PostgresPoolDrainTimeout):
            provider.close()

    provider.close(timeout=0.1)
    assert provider.state == "closed"


def test_close_wakes_a_waiter_with_pool_closed():
    provider, _ = make_provider(acquire_timeout=1)
    entered = Event()
    failures = []

    def waiter():
        entered.set()
        try:
            with provider.connection():
                pass
        except Exception as exc:
            failures.append(exc)

    with provider.connection():
        thread = Thread(target=waiter)
        thread.start()
        assert entered.wait(1)
        with pytest.raises(PostgresPoolDrainTimeout):
            provider.close(timeout=0)
        thread.join(1)

    assert len(failures) == 1
    assert isinstance(failures[0], PostgresPoolClosed)
    provider.close()


def test_closed_connection_is_discarded():
    provider, pool = make_provider()

    with provider.connection() as connection:
        connection.closed = True

    assert pool.returned[-1] == (connection, True)
    assert provider.snapshot().discard_count == 1
    provider.close()


def test_explicit_discard_exception_discards_exclusive_connection():
    provider, pool = make_provider()

    with pytest.raises(PostgresConnectionDiscarded):
        with provider.exclusive_connection(autocommit=True) as connection:
            raise PostgresConnectionDiscarded("lost session")

    assert pool.returned[-1] == (connection, True)
    provider.close()


def test_metric_callback_failure_does_not_change_connection_behavior():
    provider, _ = make_provider(
        metric_callback=lambda snapshot: (_ for _ in ()).throw(RuntimeError("metrics"))
    )

    with provider.connection():
        pass

    assert provider.snapshot().acquire_count == 1
    provider.close()


def test_direct_provider_owns_and_closes_each_connection():
    connections = []

    def connect(dsn):
        connection = FakeConnection()
        connections.append(connection)
        return connection

    provider = DirectPsycopg2ConnectionProvider("safe-dsn", connect=connect)
    with provider.connection():
        pass

    assert connections[0].commits == 1
    assert connections[0].closed is True
