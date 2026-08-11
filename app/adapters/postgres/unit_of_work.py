from __future__ import annotations

from types import TracebackType
from typing import Any

from app.ports.unit_of_work import UnitOfWorkPort
from app.services.postgres_connections import ConnectionProvider


class _RollbackOnlyExit(RuntimeError):
    """Internal marker used to make the provider roll back on normal exit."""


class PostgresUnitOfWork(UnitOfWorkPort):
    """Own one PostgreSQL connection/cursor and require an explicit decision."""

    def __init__(self, connection_provider: ConnectionProvider) -> None:
        self._provider = connection_provider
        self._connection_context: Any | None = None
        self._cursor_context: Any | None = None
        self._connection: Any | None = None
        self._cursor: Any | None = None
        self._state = "new"
        self._decision = "rollback"

    @property
    def state(self) -> str:
        return self._state

    @property
    def cursor(self):
        if self._state != "active" or self._cursor is None:
            raise RuntimeError("unit of work cursor is not active")
        return self._cursor

    def __enter__(self) -> "PostgresUnitOfWork":
        if self._state != "new":
            raise RuntimeError("unit of work cannot be reused")
        connection_context = self._provider.connection()
        connection = connection_context.__enter__()
        try:
            cursor_context = connection.cursor()
            cursor = cursor_context.__enter__()
        except BaseException as exc:
            connection_context.__exit__(type(exc), exc, exc.__traceback__)
            self._state = "closed"
            raise
        self._connection_context = connection_context
        self._cursor_context = cursor_context
        self._connection = connection
        self._cursor = cursor
        self._state = "active"
        return self

    def commit(self) -> None:
        self._require_active()
        if self._decision == "rollback_explicit":
            raise RuntimeError("rolled-back unit of work cannot commit")
        self._decision = "commit"

    def rollback(self) -> None:
        self._require_active()
        self._decision = "rollback_explicit"

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        self._require_active()
        cursor_context = self._cursor_context
        connection_context = self._connection_context
        cursor_error: BaseException | None = None
        connection_error: BaseException | None = None
        committed = False
        try:
            cursor_context.__exit__(exc_type, exc, traceback)
        except BaseException as close_error:
            cursor_error = close_error

        effective_exc_type = exc_type
        effective_exc = exc
        effective_traceback = traceback
        if effective_exc_type is None and cursor_error is not None:
            effective_exc_type = type(cursor_error)
            effective_exc = cursor_error
            effective_traceback = cursor_error.__traceback__

        try:
            if effective_exc_type is None and self._decision == "commit":
                connection_context.__exit__(None, None, None)
                committed = True
            elif effective_exc_type is not None:
                connection_context.__exit__(
                    effective_exc_type,
                    effective_exc,
                    effective_traceback,
                )
            else:
                marker = _RollbackOnlyExit("unit of work exited without commit")
                connection_context.__exit__(
                    _RollbackOnlyExit,
                    marker,
                    marker.__traceback__,
                )
        except BaseException as close_error:
            connection_error = close_error
        finally:
            self._cursor = None
            self._cursor_context = None
            self._connection = None
            self._connection_context = None
            self._state = "committed" if committed else "rolled_back"

        # A body failure remains authoritative over cleanup failures. If the
        # body succeeded, the first resource failure (cursor before
        # connection) is the stable error exposed to the caller.
        if exc_type is not None:
            return False
        if cursor_error is not None:
            raise cursor_error
        if connection_error is not None:
            raise connection_error
        return False

    def _require_active(self) -> None:
        if self._state != "active":
            raise RuntimeError("unit of work is not active")


__all__ = ["PostgresUnitOfWork"]
