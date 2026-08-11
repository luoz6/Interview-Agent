from __future__ import annotations

from contextlib import AbstractContextManager
from threading import RLock
from typing import Any, Callable

from app.runtime.config import (
    load_langgraph_strict_msgpack,
    set_default_environment_value,
)
from app.services.postgres_connections import PostgresSchemaNotReady


CHECKPOINTER_TABLES = (
    "checkpoints",
    "checkpoint_blobs",
    "checkpoint_writes",
    "checkpoint_migrations",
)


class PostgresCheckpointerRuntime:
    """Own a pooled synchronous PostgresSaver without performing runtime DDL."""

    def __init__(
        self,
        dsn: str,
        *,
        min_size: int = 1,
        max_size: int = 2,
        acquire_timeout: float = 2.0,
        shutdown_timeout: float = 5.0,
        connect_timeout: int = 3,
        max_lifetime: float = 1800.0,
        max_idle: float = 300.0,
        pool_factory: Callable[..., Any] | None = None,
        saver_factory: Callable[[str], AbstractContextManager] | None = None,
        schema_validator: Callable[[Any], None] | None = None,
    ) -> None:
        if not dsn:
            raise ValueError("dsn is required")
        if min_size < 0 or max_size < 1 or min_size > max_size:
            raise ValueError("invalid Checkpointer pool bounds")
        if acquire_timeout <= 0 or shutdown_timeout <= 0 or connect_timeout <= 0:
            raise ValueError("Checkpointer pool timeouts must be positive")
        if max_lifetime <= 0 or max_idle <= 0:
            raise ValueError("Checkpointer lifetime and idle limits must be positive")
        if pool_factory is not None and saver_factory is not None:
            raise ValueError("pool_factory and saver_factory are mutually exclusive")
        self.dsn = dsn
        self.min_size = min_size
        self.max_size = max_size
        self.acquire_timeout = acquire_timeout
        self.shutdown_timeout = shutdown_timeout
        self.connect_timeout = int(connect_timeout)
        self.max_lifetime = max_lifetime
        self.max_idle = max_idle
        self._pool_factory = pool_factory
        # Legacy context injection is retained only for isolated unit tests.
        self._legacy_saver_factory = saver_factory
        self._schema_validator = schema_validator or validate_checkpointer_schema
        self._context: AbstractContextManager | None = None
        self._pool: Any | None = None
        self._saver: Any | None = None
        self._state = "new"
        self._lock = RLock()

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    def start(self):
        with self._lock:
            if self._state == "open":
                return self._saver
            if self._state != "new":
                raise RuntimeError("LangGraph checkpointer cannot be restarted")
            self._state = "starting"
        set_default_environment_value(
            "LANGGRAPH_STRICT_MSGPACK",
            "true" if load_langgraph_strict_msgpack() else "false",
        )

        try:
            if self._legacy_saver_factory is not None:
                context = self._legacy_saver_factory(self.dsn)
                saver = context.__enter__()
                pool = None
            else:
                pool = self._build_pool()
                pool.open(wait=True, timeout=self.acquire_timeout)
                self._schema_validator(pool)
                from langgraph.checkpoint.postgres import PostgresSaver

                saver = PostgresSaver(pool)
                context = None
        except BaseException:
            if "pool" in locals() and pool is not None:
                try:
                    pool.close(timeout=self.shutdown_timeout)
                except Exception:
                    pass
            with self._lock:
                self._state = "new"
            raise

        with self._lock:
            self._context = context
            self._pool = pool
            self._saver = saver
            self._state = "open"
            return saver

    @property
    def saver(self):
        with self._lock:
            if self._state != "open" or self._saver is None:
                raise RuntimeError("LangGraph checkpointer is not started")
            return self._saver

    @property
    def pool(self):
        with self._lock:
            if self._state != "open" or self._pool is None:
                raise RuntimeError("pooled LangGraph checkpointer is not started")
            return self._pool

    def delete_thread(self, session_id: str) -> None:
        self.saver.delete_thread(session_id)

    def shutdown(self) -> None:
        with self._lock:
            if self._state == "closed":
                return
            if self._state == "new":
                self._state = "closed"
                return
            if self._state not in {"open", "closing"}:
                raise RuntimeError("LangGraph checkpointer lifecycle is busy")
            self._state = "closing"
            context = self._context
            pool = self._pool

        if context is not None:
            context.__exit__(None, None, None)
            with self._lock:
                if self._context is context:
                    self._context = None
        if pool is not None:
            pool.close(timeout=self.shutdown_timeout)
            with self._lock:
                if self._pool is pool:
                    self._pool = None
        with self._lock:
            self._saver = None
            self._state = "closed"

    def _build_pool(self):
        factory = self._pool_factory
        if factory is None:
            from psycopg_pool import ConnectionPool

            factory = ConnectionPool
        from psycopg.rows import dict_row

        return factory(
            conninfo=self.dsn,
            min_size=self.min_size,
            max_size=self.max_size,
            open=False,
            timeout=self.acquire_timeout,
            max_lifetime=self.max_lifetime,
            max_idle=self.max_idle,
            name="interview_checkpointer",
            kwargs={
                "autocommit": True,
                "prepare_threshold": 0,
                "row_factory": dict_row,
                "application_name": "interview_checkpointer",
                "connect_timeout": self.connect_timeout,
            },
        )


def validate_checkpointer_schema(pool: Any) -> None:
    """Read-only runtime gate for tables created by the migration command."""

    with pool.connection(timeout=2.0) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT to_regclass('public.' || name) "
                "FROM unnest(%s::text[]) AS name",
                (list(CHECKPOINTER_TABLES),),
            )
            rows = cursor.fetchall()
            cursor.execute(
                "SELECT table_name, column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' "
                "AND table_name = ANY(%s::text[])",
                (list(CHECKPOINTER_TABLES),),
            )
            column_rows = cursor.fetchall()
            cursor.execute("SELECT MAX(v) FROM checkpoint_migrations")
            migration_row = cursor.fetchone()
    # Some test/future drivers may return dictionaries because dict_row is
    # configured; normalize both supported row representations.
    present = []
    for row in rows:
        if isinstance(row, dict):
            present.append(next(iter(row.values())))
        else:
            present.append(row[0])
    if len(present) != len(CHECKPOINTER_TABLES) or any(
        value is None for value in present
    ):
        raise PostgresSchemaNotReady(
            "LangGraph Checkpointer schema is not ready"
        )
    required_columns = {
        "checkpoint_migrations": {"v"},
        "checkpoints": {
            "thread_id",
            "checkpoint_ns",
            "checkpoint_id",
            "checkpoint",
            "metadata",
        },
        "checkpoint_blobs": {
            "thread_id",
            "checkpoint_ns",
            "channel",
            "version",
            "type",
            "blob",
        },
        "checkpoint_writes": {
            "thread_id",
            "checkpoint_ns",
            "checkpoint_id",
            "task_id",
            "task_path",
            "idx",
            "channel",
            "blob",
        },
    }
    columns = {name: set() for name in CHECKPOINTER_TABLES}
    for row in column_rows:
        if isinstance(row, dict):
            values = list(row.values())
            table_name, column_name = values[0], values[1]
        else:
            table_name, column_name = row[0], row[1]
        columns.setdefault(table_name, set()).add(column_name)
    if any(
        not required.issubset(columns.get(table_name, set()))
        for table_name, required in required_columns.items()
    ):
        raise PostgresSchemaNotReady(
            "LangGraph Checkpointer schema is incompatible"
        )
    from langgraph.checkpoint.postgres import PostgresSaver

    latest_expected = len(PostgresSaver.MIGRATIONS) - 1
    latest_applied = (
        next(iter(migration_row.values()))
        if isinstance(migration_row, dict)
        else migration_row[0]
    )
    if latest_applied is None or int(latest_applied) < latest_expected:
        raise PostgresSchemaNotReady(
            "LangGraph Checkpointer migration is incomplete"
        )


class VersionedGraphRegistry:
    def __init__(self) -> None:
        self._graphs: dict[str, Any] = {}

    def register(self, graph_schema_version: str, graph: Any) -> None:
        if graph_schema_version in self._graphs:
            raise ValueError(
                f"graph already registered: {graph_schema_version}"
            )
        self._graphs[graph_schema_version] = graph

    def get(self, graph_schema_version: str):
        try:
            return self._graphs[graph_schema_version]
        except KeyError as exc:
            raise ValueError(
                f"unsupported graph version: {graph_schema_version}"
            ) from exc


# Kept while interview callers migrate to the generic registry name.
VersionedInterviewGraphRegistry = VersionedGraphRegistry
