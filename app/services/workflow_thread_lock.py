from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass
from threading import Lock
from time import monotonic, sleep
from typing import Any, Callable

from app.services.postgres_connections import (
    DirectPsycopg2ConnectionProvider,
    ExclusiveConnectionProvider,
    PostgresPoolExhausted,
)


class WorkflowThreadBusy(RuntimeError):
    """The workflow thread is currently owned by another executor."""


class WorkflowThreadLockLost(RuntimeError):
    """The dedicated PostgreSQL session that owned the lock was lost."""


class GenerationLeaseLost(RuntimeError):
    """The active generation attempt lease is no longer owned."""


class ReportLeaseLost(RuntimeError):
    """The active Report Job lease is no longer owned."""


class FencedWriteRejected(RuntimeError):
    """A stale owner attempted a write guarded by a fencing predicate."""


class ReviewEffectLeaseLost(FencedWriteRejected):
    """The active Review Effect claim is no longer provably owned."""


class ReviewEffectBusy(RuntimeError):
    """A review provider operation is owned by another live claim."""


class ReviewEffectConflict(RuntimeError):
    """An effect operation key was reused with conflicting immutable data."""


class ProjectionConflict(RuntimeError):
    """LangGraph state diverged from its authoritative business projection."""


class ReportCommitConflict(RuntimeError):
    """The fenced final Review projections could not commit atomically."""


def _validated_identifier(value: str, *, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def interview_thread_identity(session_id: str) -> str:
    return f"interview:{_validated_identifier(session_id, name='session_id')}"


def review_thread_identity(job_id: str) -> str:
    return f"review:{_validated_identifier(job_id, name='job_id')}"


def advisory_lock_key(identity: str) -> int:
    canonical = _validated_identifier(identity, name="identity")
    digest = hashlib.sha256(canonical.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


@dataclass(frozen=True)
class WorkflowThreadOwnership:
    workflow_type: str
    wait_seconds: float
    _connection: Any

    def ensure_owned(self) -> None:
        if getattr(self._connection, "closed", True):
            raise WorkflowThreadLockLost(
                "workflow thread lock connection was lost"
            )
        try:
            with self._connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchone()
        except Exception as exc:
            raise WorkflowThreadLockLost(
                "workflow thread lock connection was lost"
            ) from exc


class NoopWorkflowThreadLock:
    """Compatibility lock for isolated unit tests and non-PostgreSQL callers."""

    def hold(
        self,
        identity: str,
        *,
        workflow_type: str,
        timeout_seconds: float | None = None,
    ):
        _validated_identifier(identity, name="identity")
        _validated_identifier(workflow_type, name="workflow_type")
        return _NoopWorkflowThreadLockContext()

    def close(self) -> None:
        return None


class PostgresWorkflowThreadLock:
    def __init__(
        self,
        *,
        dsn: str | None = None,
        exclusive_provider: ExclusiveConnectionProvider | None = None,
        default_timeout_seconds: float = 1.0,
        initial_backoff_seconds: float = 0.01,
        max_backoff_seconds: float = 0.1,
        jitter_ratio: float = 0.2,
        metric_callback: Callable[[str, str, float], None] | None = None,
        connect: Callable[..., Any] | None = None,
        monotonic_fn: Callable[[], float] = monotonic,
        sleep_fn: Callable[[float], None] = sleep,
        random_fn: Callable[[], float] = random.random,
    ) -> None:
        if exclusive_provider is not None and connect is not None:
            raise ValueError("exclusive_provider and connect are mutually exclusive")
        if exclusive_provider is None and connect is None and not dsn:
            raise ValueError("dsn or exclusive_provider is required")
        if default_timeout_seconds < 0:
            raise ValueError("default_timeout_seconds must be non-negative")
        if initial_backoff_seconds <= 0 or max_backoff_seconds <= 0:
            raise ValueError("lock backoff must be positive")
        if max_backoff_seconds < initial_backoff_seconds:
            raise ValueError("max_backoff_seconds must not be smaller than initial backoff")
        if not 0 <= jitter_ratio <= 1:
            raise ValueError("jitter_ratio must be between zero and one")
        self.dsn = dsn or ""
        self.default_timeout_seconds = default_timeout_seconds
        self.initial_backoff_seconds = initial_backoff_seconds
        self.max_backoff_seconds = max_backoff_seconds
        self.jitter_ratio = jitter_ratio
        self.metric_callback = metric_callback
        self._connect = connect
        self._exclusive_provider = exclusive_provider
        if exclusive_provider is None and connect is None:
            self._exclusive_provider = DirectPsycopg2ConnectionProvider(
                self.dsn
            )
        self._monotonic = monotonic_fn
        self._sleep = sleep_fn
        self._random = random_fn
        self._lifecycle_lock = Lock()
        self._closed = False

    def _connection(self):
        if self._connect is not None:
            return self._connect(self.dsn)
        raise RuntimeError("legacy connection factory is not configured")

    def _exclusive_connection(self):
        if self._exclusive_provider is not None:
            return self._exclusive_provider.exclusive_connection(
                autocommit=True
            )
        return _LegacyExclusiveConnectionContext(self)

    def _record(self, workflow_type: str, outcome: str, wait_seconds: float) -> None:
        if self.metric_callback is not None:
            try:
                self.metric_callback(
                    workflow_type,
                    outcome,
                    max(0.0, wait_seconds),
                )
            except Exception:
                # Lock metrics are observational and cannot change ownership.
                return

    def hold(
        self,
        identity: str,
        *,
        workflow_type: str,
        timeout_seconds: float | None = None,
    ):
        canonical = _validated_identifier(identity, name="identity")
        workflow_kind = _validated_identifier(
            workflow_type, name="workflow_type"
        )
        timeout = (
            self.default_timeout_seconds
            if timeout_seconds is None
            else timeout_seconds
        )
        if timeout < 0:
            raise ValueError("timeout_seconds must be non-negative")
        return _PostgresWorkflowThreadLockContext(
            owner=self,
            canonical=canonical,
            workflow_kind=workflow_kind,
            timeout=timeout,
        )

    def close(self) -> None:
        with self._lifecycle_lock:
            self._closed = True


class _NoopWorkflowThreadLockContext:
    def __enter__(self):
        return None

    def __exit__(self, exc_type, exc, traceback):
        return False


class _LegacyExclusiveConnectionContext:
    def __init__(self, owner: PostgresWorkflowThreadLock) -> None:
        self.owner = owner
        self.connection = None

    def __enter__(self):
        connection = self.owner._connection()
        connection.autocommit = True
        self.connection = connection
        return connection

    def __exit__(self, exc_type, exc, traceback):
        connection = self.connection
        if connection is not None:
            try:
                connection.close()
            except Exception:
                if exc_type is None:
                    raise
        return False


class _PostgresWorkflowThreadLockContext:
    def __init__(
        self,
        *,
        owner: PostgresWorkflowThreadLock,
        canonical: str,
        workflow_kind: str,
        timeout: float,
    ) -> None:
        self.owner = owner
        self.canonical = canonical
        self.workflow_kind = workflow_kind
        self.timeout = timeout
        self.key = advisory_lock_key(canonical)
        self.connection_context = None
        self.connection = None
        self.acquired = False

    def __enter__(self) -> WorkflowThreadOwnership:
        with self.owner._lifecycle_lock:
            if self.owner._closed:
                raise WorkflowThreadLockLost(
                    "workflow thread lock service is closed"
                )
        self.connection_context = self.owner._exclusive_connection()
        try:
            self.connection = self.connection_context.__enter__()
        except PostgresPoolExhausted:
            self.owner._record(self.workflow_kind, "pool_exhausted", 0.0)
            raise

        try:
            started = self.owner._monotonic()
            deadline = started + self.timeout
            backoff = self.owner.initial_backoff_seconds
            while True:
                try:
                    with self.connection.cursor() as cursor:
                        cursor.execute(
                            "SELECT pg_try_advisory_lock(%s)",
                            (self.key,),
                        )
                        row = cursor.fetchone()
                except Exception as exc:
                    try:
                        self.connection.close()
                    except Exception:
                        pass
                    raise WorkflowThreadLockLost(
                        "workflow thread lock connection was lost"
                    ) from exc
                if row and bool(row[0]):
                    self.acquired = True
                    break
                now = self.owner._monotonic()
                if now >= deadline:
                    waited = now - started
                    self.owner._record(self.workflow_kind, "busy", waited)
                    raise WorkflowThreadBusy(
                        "workflow thread is owned by another executor"
                    )
                jitter = 1 + self.owner.jitter_ratio * (
                    2 * self.owner._random() - 1
                )
                delay = min(
                    backoff * jitter,
                    max(0.0, deadline - now),
                )
                self.owner._sleep(max(0.0, delay))
                backoff = min(
                    backoff * 2,
                    self.owner.max_backoff_seconds,
                )

            waited = self.owner._monotonic() - started
            self.owner._record(self.workflow_kind, "acquired", waited)
            return WorkflowThreadOwnership(
                workflow_type=self.workflow_kind,
                wait_seconds=max(0.0, waited),
                _connection=self.connection,
            )
        except BaseException as exc:
            self._close_connection_context(type(exc), exc, exc.__traceback__)
            raise

    def __exit__(self, exc_type, exc, traceback):
        unlock_error: Exception | None = None
        connection = self.connection
        if self.acquired and not getattr(connection, "closed", True):
            try:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT pg_advisory_unlock(%s)",
                        (self.key,),
                    )
                    row = cursor.fetchone()
                if not row or not bool(row[0]):
                    unlock_error = WorkflowThreadLockLost(
                        "workflow thread lock ownership was lost"
                    )
            except Exception as unlock_cause:
                unlock_error = WorkflowThreadLockLost(
                    "workflow thread lock connection was lost"
                )
                unlock_error.__cause__ = unlock_cause
        if unlock_error is not None:
            try:
                connection.close()
            except Exception:
                pass

        close_error = self._close_connection_context(exc_type, exc, traceback)
        if exc_type is not None:
            return False
        if unlock_error is not None:
            raise unlock_error
        if close_error is not None:
            raise close_error
        return False

    def _close_connection_context(self, exc_type, exc, traceback):
        context, self.connection_context = self.connection_context, None
        if context is None:
            return None
        try:
            context.__exit__(exc_type, exc, traceback)
        except BaseException as close_error:
            if exc_type is None:
                return close_error
        return None
