from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from typing import Any, Callable

from app.services.config import PostgresPoolSettings
from app.services.langgraph_runtime import PostgresCheckpointerRuntime
from app.services.postgres_connections import (
    PooledPsycopg2ConnectionProvider,
    PostgresPoolSnapshot,
)


@dataclass(frozen=True)
class PostgresConnectionDomainSnapshot:
    business: PostgresPoolSnapshot
    telemetry: PostgresPoolSnapshot
    advisory_lock: PostgresPoolSnapshot
    checkpointer_state: str


class PostgresConnectionDomains:
    """The sole production owner of all process-local PostgreSQL pools."""

    def __init__(
        self,
        *,
        dsn: str,
        settings: PostgresPoolSettings,
        psycopg2_pool_factory: Callable[..., Any] | None = None,
        checkpointer_pool_factory: Callable[..., Any] | None = None,
        checkpointer_schema_validator: Callable[[Any], None] | None = None,
    ) -> None:
        self.dsn = dsn
        self.settings = settings
        common = {
            "dsn": dsn,
            "drain_timeout": settings.drain_timeout_seconds,
            "connect_timeout": settings.connect_timeout_seconds,
            "max_lifetime": settings.max_lifetime_seconds,
            "max_idle": settings.max_idle_seconds,
            "pool_factory": psycopg2_pool_factory,
        }
        self.business = PooledPsycopg2ConnectionProvider(
            domain="business",
            min_size=settings.business_min_size,
            max_size=settings.business_max_size,
            acquire_timeout=settings.business_acquire_timeout_seconds,
            **common,
        )
        self.telemetry = PooledPsycopg2ConnectionProvider(
            domain="telemetry",
            min_size=settings.telemetry_min_size,
            max_size=settings.telemetry_max_size,
            acquire_timeout=settings.telemetry_acquire_timeout_seconds,
            **common,
        )
        self.advisory_lock = PooledPsycopg2ConnectionProvider(
            domain="lock",
            min_size=settings.lock_min_size,
            max_size=settings.lock_max_size,
            acquire_timeout=settings.lock_acquire_timeout_seconds,
            **common,
        )
        self.checkpointer = PostgresCheckpointerRuntime(
            dsn,
            min_size=settings.checkpointer_min_size,
            max_size=settings.checkpointer_max_size,
            acquire_timeout=settings.checkpointer_acquire_timeout_seconds,
            shutdown_timeout=settings.drain_timeout_seconds,
            connect_timeout=settings.connect_timeout_seconds,
            max_lifetime=settings.max_lifetime_seconds,
            max_idle=settings.max_idle_seconds,
            pool_factory=checkpointer_pool_factory,
            schema_validator=checkpointer_schema_validator,
        )
        self._state = "new"
        self._lock = RLock()

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    def open(self) -> None:
        with self._lock:
            if self._state == "open":
                return
            if self._state != "new":
                raise RuntimeError("PostgreSQL connection domains cannot reopen")
            self._state = "opening"
        opened = []
        try:
            for provider in (self.business, self.telemetry, self.advisory_lock):
                provider.open()
                opened.append(provider)
        except BaseException:
            for provider in reversed(opened):
                try:
                    provider.close(timeout=self.settings.drain_timeout_seconds)
                except Exception:
                    pass
            with self._lock:
                self._state = "new"
            raise
        with self._lock:
            self._state = "open"

    def snapshot(self) -> PostgresConnectionDomainSnapshot:
        return PostgresConnectionDomainSnapshot(
            business=self.business.snapshot(),
            telemetry=self.telemetry.snapshot(),
            advisory_lock=self.advisory_lock.snapshot(),
            checkpointer_state=self.checkpointer.state,
        )

    def close(self) -> None:
        with self._lock:
            if self._state == "closed":
                return
            self._state = "closing"
        errors = []
        for owner in (
            self.checkpointer,
            self.advisory_lock,
            self.telemetry,
            self.business,
        ):
            try:
                if owner is self.checkpointer:
                    owner.shutdown()
                else:
                    owner.close(timeout=self.settings.drain_timeout_seconds)
            except Exception as exc:
                errors.append(exc)
        if errors:
            # A child owner that timed out remains retryable. Keep the aggregate
            # owner in closing so a later shutdown call can finish draining it.
            raise errors[0]
        with self._lock:
            self._state = "closed"
