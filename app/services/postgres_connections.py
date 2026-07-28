from __future__ import annotations

from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass
from math import ceil
from threading import Condition, Lock
from time import monotonic, perf_counter
from typing import Any, Callable, Iterator, Protocol, runtime_checkable


class PostgresConnectionError(RuntimeError):
    """Base class for stable, privacy-safe connection-domain failures."""


class PostgresPoolExhausted(PostgresConnectionError):
    pass


class PostgresPoolClosed(PostgresConnectionError):
    pass


class PostgresConnectionDiscarded(PostgresConnectionError):
    pass


class PostgresSchemaNotReady(PostgresConnectionError):
    pass


class PostgresPoolDrainTimeout(PostgresConnectionError):
    pass


@runtime_checkable
class ConnectionProvider(Protocol):
    @contextmanager
    def connection(self) -> Iterator[Any]: ...


@runtime_checkable
class ExclusiveConnectionProvider(Protocol):
    @contextmanager
    def exclusive_connection(self, *, autocommit: bool) -> Iterator[Any]: ...


@dataclass(frozen=True)
class PostgresPoolSnapshot:
    domain: str
    min_size: int
    max_size: int
    leased: int
    idle: int
    waiting: int
    peak_leased: int
    acquire_count: int
    acquire_timeout_count: int
    discard_count: int
    total_wait_ms: float
    max_wait_ms: float
    wait_samples: int
    p50_wait_ms: float
    p95_wait_ms: float
    state: str


@dataclass
class _MutableMetrics:
    waiting: int = 0
    peak_leased: int = 0
    acquire_count: int = 0
    acquire_timeout_count: int = 0
    discard_count: int = 0
    total_wait_ms: float = 0.0
    max_wait_ms: float = 0.0


class DirectPsycopg2ConnectionProvider:
    """Compatibility provider that owns each connection it opens."""

    def __init__(
        self,
        dsn: str,
        *,
        connect: Callable[..., Any] | None = None,
        connect_kwargs: dict[str, Any] | None = None,
    ) -> None:
        self._dsn = dsn
        self._connect = connect
        self._connect_kwargs = dict(connect_kwargs or {})

    def _open_connection(self):
        if self._connect is None:
            import psycopg2

            connect = psycopg2.connect
        else:
            connect = self._connect
        return connect(self._dsn, **self._connect_kwargs)

    def connection(self) -> Iterator[Any]:
        return _DirectConnectionContext(self, autocommit=False)

    def exclusive_connection(self, *, autocommit: bool) -> Iterator[Any]:
        return _DirectConnectionContext(self, autocommit=autocommit)

    def close(self, timeout: float | None = None) -> None:
        return None


class PooledPsycopg2ConnectionProvider:
    """Explicitly opened, bounded provider around ThreadedConnectionPool.

    The Condition reserves checkout capacity before calling getconn(). This
    adds deterministic wait/timeout semantics that psycopg2's pool does not
    provide itself.
    """

    def __init__(
        self,
        dsn: str,
        *,
        domain: str,
        min_size: int = 1,
        max_size: int = 5,
        acquire_timeout: float = 2.0,
        drain_timeout: float = 5.0,
        connect_timeout: int = 3,
        max_lifetime: float = 1800.0,
        max_idle: float = 300.0,
        application_name: str | None = None,
        pool_factory: Callable[..., Any] | None = None,
        metric_callback: Callable[[PostgresPoolSnapshot], None] | None = None,
        wait_sample_limit: int = 256,
        monotonic_fn: Callable[[], float] = monotonic,
    ) -> None:
        if min_size < 0:
            raise ValueError("min_size must be non-negative")
        if max_size < 1 or min_size > max_size:
            raise ValueError("max_size must be positive and at least min_size")
        if acquire_timeout <= 0 or drain_timeout <= 0 or connect_timeout <= 0:
            raise ValueError("pool timeouts must be positive")
        if max_lifetime <= 0 or max_idle <= 0:
            raise ValueError("pool lifetime and idle limits must be positive")
        if wait_sample_limit < 1:
            raise ValueError("wait_sample_limit must be positive")
        if not domain or not domain.replace("_", "").isalnum():
            raise ValueError("domain must be a stable safe label")

        self._dsn = dsn
        self.domain = domain
        self.min_size = min_size
        self.max_size = max_size
        self.acquire_timeout = acquire_timeout
        self.drain_timeout = drain_timeout
        self.connect_timeout = int(connect_timeout)
        self.max_lifetime = max_lifetime
        self.max_idle = max_idle
        self.application_name = application_name or f"interview_{domain}"
        self._pool_factory = pool_factory
        self._metric_callback = metric_callback
        self._condition = Condition(Lock())
        self._pool: Any | None = None
        self._state = "new"
        self._reserved_count = 0
        self._metrics = _MutableMetrics()
        self._wait_samples: deque[float] = deque(maxlen=wait_sample_limit)
        self._monotonic = monotonic_fn
        self._connection_created_at: dict[int, float] = {}
        self._connection_last_returned_at: dict[int, float] = {}

    @property
    def state(self) -> str:
        with self._condition:
            return self._state

    def open(self) -> None:
        with self._condition:
            if self._state == "open":
                return
            if self._state != "new":
                raise PostgresPoolClosed("PostgreSQL pool cannot be reopened")
            # Reserve the lifecycle transition, but do not hold the Condition
            # while psycopg2 creates its minimum physical connections.
            self._state = "opening"

        try:
            factory = self._pool_factory
            if factory is None:
                from psycopg2.pool import ThreadedConnectionPool

                factory = ThreadedConnectionPool
            pool = factory(
                self.min_size,
                self.max_size,
                self._dsn,
                application_name=self.application_name,
                connect_timeout=self.connect_timeout,
            )
        except BaseException:
            with self._condition:
                self._state = "new"
                self._condition.notify_all()
            raise

        with self._condition:
            self._pool = pool
            self._state = "open"
            self._condition.notify_all()
        self._emit_metrics()

    def connection(self) -> Iterator[Any]:
        return _PooledConnectionContext(self, autocommit=False)

    def exclusive_connection(self, *, autocommit: bool) -> Iterator[Any]:
        return _PooledConnectionContext(self, autocommit=autocommit)

    def discard(self, connection: Any) -> None:
        """Discard a currently leased raw connection.

        This is intended for the advisory-lock owner when a session can no
        longer prove unlock/ownership. Normal Store code must use the context
        managers instead.
        """

        self._return(connection, discard=True)

    def _checkout(self):
        started = perf_counter()
        with self._condition:
            if self._state != "open":
                raise PostgresPoolClosed("PostgreSQL pool is not open")
            self._metrics.waiting += 1
            try:
                available = self._condition.wait_for(
                    lambda: self._state != "open"
                    or self._reserved_count < self.max_size,
                    timeout=self.acquire_timeout,
                )
                if not available:
                    self._metrics.acquire_timeout_count += 1
                    raise PostgresPoolExhausted(
                        "PostgreSQL pool acquire timed out"
                    )
                if self._state != "open":
                    raise PostgresPoolClosed("PostgreSQL pool is closing")
                self._reserved_count += 1
                self._metrics.peak_leased = max(
                    self._metrics.peak_leased, self._reserved_count
                )
            finally:
                self._metrics.waiting -= 1

        pool = self._pool
        try:
            connection = pool.getconn()
            connection = self._recycle_if_expired(pool, connection)
        except BaseException:
            with self._condition:
                self._reserved_count -= 1
                self._condition.notify()
            self._record_wait(started, acquired=False)
            raise

        self._record_wait(started, acquired=True)
        return connection

    def _recycle_if_expired(self, pool: Any, connection: Any):
        now = self._monotonic()
        identity = id(connection)
        with self._condition:
            created_at = self._connection_created_at.setdefault(identity, now)
            returned_at = self._connection_last_returned_at.get(identity)
            expired = now - created_at >= self.max_lifetime
            idle = returned_at is not None and now - returned_at >= self.max_idle
        if not expired and not idle:
            return connection

        pool.putconn(connection, close=True)
        with self._condition:
            self._connection_created_at.pop(identity, None)
            self._connection_last_returned_at.pop(identity, None)
            self._metrics.discard_count += 1
        replacement = pool.getconn()
        with self._condition:
            self._connection_created_at[id(replacement)] = self._monotonic()
        return replacement

    def _prepare_for_return(self, connection: Any) -> bool:
        if getattr(connection, "closed", False):
            return False
        try:
            # rollback is harmless for an idle psycopg2 session and clears any
            # transaction state left by commit failures or caller SQL.
            connection.rollback()
            _set_autocommit(connection, False)
        except BaseException:
            return False
        return not bool(getattr(connection, "closed", False))

    def _return(self, connection: Any, *, discard: bool) -> None:
        pool = self._pool
        put_error: BaseException | None = None
        try:
            pool.putconn(connection, close=discard)
        except BaseException as exc:
            put_error = exc
            _best_effort(connection.close)
            discard = True
        finally:
            with self._condition:
                if self._reserved_count <= 0:
                    raise RuntimeError("PostgreSQL pool lease accounting underflow")
                self._reserved_count -= 1
                if discard:
                    self._metrics.discard_count += 1
                    self._connection_created_at.pop(id(connection), None)
                    self._connection_last_returned_at.pop(id(connection), None)
                else:
                    if id(connection) not in self._connection_created_at:
                        self._connection_created_at[id(connection)] = (
                            self._monotonic()
                        )
                    self._connection_last_returned_at[id(connection)] = (
                        self._monotonic()
                    )
                self._condition.notify_all()
            self._emit_metrics()
        if put_error is not None:
            raise PostgresConnectionDiscarded(
                "PostgreSQL connection return failed"
            ) from put_error

    def close(self, timeout: float | None = None) -> None:
        resolved_timeout = self.drain_timeout if timeout is None else timeout
        if resolved_timeout < 0:
            raise ValueError("close timeout must be non-negative")
        deadline = self._monotonic() + resolved_timeout
        with self._condition:
            if self._state == "closed":
                return
            if self._state == "new":
                self._state = "closed"
                self._condition.notify_all()
                return
            self._state = "closing"
            self._condition.notify_all()
            drained = self._condition.wait_for(
                lambda: self._reserved_count == 0,
                timeout=max(0.0, deadline - self._monotonic()),
            )
            if not drained:
                raise PostgresPoolDrainTimeout(
                    "PostgreSQL pool did not drain before shutdown timeout"
                )
            pool = self._pool

        # No new leases can enter after state=closing, and all prior leases
        # have drained, so closeall cannot terminate in-use work.
        pool.closeall()
        with self._condition:
            self._state = "closed"
            self._pool = None
            self._connection_created_at.clear()
            self._connection_last_returned_at.clear()
            self._condition.notify_all()
        self._emit_metrics()

    def snapshot(self) -> PostgresPoolSnapshot:
        with self._condition:
            samples = tuple(self._wait_samples)
            leased = self._reserved_count
            metrics = _MutableMetrics(**vars(self._metrics))
            state = self._state
            pool = self._pool
        raw_idle = getattr(pool, "_pool", None) if pool is not None else None
        if raw_idle is None and pool is not None:
            raw_idle = getattr(pool, "available", None)
        idle = len(raw_idle) if raw_idle is not None else 0
        return PostgresPoolSnapshot(
            domain=self.domain,
            min_size=self.min_size,
            max_size=self.max_size,
            leased=leased,
            idle=idle,
            waiting=metrics.waiting,
            peak_leased=metrics.peak_leased,
            acquire_count=metrics.acquire_count,
            acquire_timeout_count=metrics.acquire_timeout_count,
            discard_count=metrics.discard_count,
            total_wait_ms=round(metrics.total_wait_ms, 3),
            max_wait_ms=round(metrics.max_wait_ms, 3),
            wait_samples=len(samples),
            p50_wait_ms=_percentile(samples, 0.50),
            p95_wait_ms=_percentile(samples, 0.95),
            state=state,
        )

    def _record_wait(self, started: float, *, acquired: bool) -> None:
        elapsed_ms = max(0.0, (perf_counter() - started) * 1000.0)
        with self._condition:
            self._wait_samples.append(elapsed_ms)
            self._metrics.total_wait_ms += elapsed_ms
            self._metrics.max_wait_ms = max(self._metrics.max_wait_ms, elapsed_ms)
            if acquired:
                self._metrics.acquire_count += 1
        self._emit_metrics()

    def _emit_metrics(self) -> None:
        if self._metric_callback is None:
            return
        try:
            self._metric_callback(self.snapshot())
        except Exception:
            # Metrics are best effort and cannot change connection ownership.
            return


def _set_autocommit(connection: Any, value: bool) -> None:
    if getattr(connection, "autocommit", None) != value:
        connection.autocommit = value


class _DirectConnectionContext:
    def __init__(
        self,
        provider: DirectPsycopg2ConnectionProvider,
        *,
        autocommit: bool,
    ) -> None:
        self.provider = provider
        self.autocommit = autocommit
        self.connection: Any | None = None

    def __enter__(self):
        connection = self.provider._open_connection()
        _set_autocommit(connection, self.autocommit)
        self.connection = connection
        return connection

    def __exit__(self, exc_type, exc, traceback):
        connection = self.connection
        try:
            if exc_type is None:
                if not self.autocommit:
                    connection.commit()
            elif not self.autocommit:
                _best_effort(connection.rollback)
        finally:
            try:
                connection.close()
            except Exception:
                # Connection cleanup cannot replace the caller's business
                # exception. A successful body still reports close failure.
                if exc_type is None:
                    raise
        return False


class _PooledConnectionContext:
    def __init__(
        self,
        provider: PooledPsycopg2ConnectionProvider,
        *,
        autocommit: bool,
    ) -> None:
        self.provider = provider
        self.autocommit = autocommit
        self.connection: Any | None = None

    def __enter__(self):
        connection = self.provider._checkout()
        try:
            _set_autocommit(connection, self.autocommit)
        except BaseException:
            self.provider._return(connection, discard=True)
            raise
        self.connection = connection
        return connection

    def __exit__(self, exc_type, exc, traceback):
        connection = self.connection
        discard = exc_type is not None and issubclass(
            exc_type, PostgresConnectionDiscarded
        )
        pending_error: BaseException | None = None
        try:
            if exc_type is None and not self.autocommit:
                try:
                    connection.commit()
                except BaseException as commit_error:
                    pending_error = commit_error
                    try:
                        connection.rollback()
                    except BaseException:
                        discard = True
            elif exc_type is not None and not self.autocommit:
                try:
                    connection.rollback()
                except BaseException:
                    discard = True
        finally:
            if not discard:
                discard = not self.provider._prepare_for_return(connection)
            try:
                self.provider._return(connection, discard=discard)
            except BaseException:
                # The caller's business failure remains authoritative. A
                # return failure may replace only a successful body.
                if exc_type is None and pending_error is None:
                    raise
        if pending_error is not None:
            raise pending_error
        return False


def _best_effort(callback: Callable[[], Any]) -> None:
    try:
        callback()
    except Exception:
        return


def _percentile(samples: tuple[float, ...], percentile: float) -> float:
    if not samples:
        return 0.0
    ordered = sorted(samples)
    index = max(0, ceil(len(ordered) * percentile) - 1)
    return round(ordered[index], 3)
