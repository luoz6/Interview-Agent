from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache
import os
import re
from typing import Callable, Iterator
from uuid import uuid4

import pytest


SAFE_TEST_PREFIX = re.compile(r"^test_[a-z0-9_]+_[0-9a-f]{12}$")
_ACTIVE_TEST_PREFIXES: ContextVar[set[str] | None] = ContextVar(
    "active_postgres_test_prefixes",
    default=None,
)
_ACTIVE_SCOPE_OPENER: ContextVar[Callable[[str], None] | None] = ContextVar(
    "active_postgres_scope_opener",
    default=None,
)


@lru_cache(maxsize=1)
def _checked_postgres_dsn() -> str | None:
    dsn = os.getenv("POSTGRES_DSN")
    if not dsn:
        return None
    import psycopg2

    try:
        with psycopg2.connect(dsn, connect_timeout=3) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                if cursor.fetchone() != (1,):
                    raise RuntimeError("PostgreSQL reachability probe returned an invalid row")
    except Exception as exc:
        raise RuntimeError("configured POSTGRES_DSN is unreachable") from exc
    return dsn


def require_postgres_dsn() -> str:
    if not os.getenv("POSTGRES_DSN", "").strip():
        pytest.skip("POSTGRES_DSN is not configured")
    try:
        dsn = _checked_postgres_dsn()
    except RuntimeError as exc:
        pytest.fail(str(exc), pytrace=False)
    if dsn is None:
        pytest.fail("configured POSTGRES_DSN resolved to an empty value", pytrace=False)
    return dsn


def make_runtime_table_prefix(scope: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", scope.lower()).strip("_")
    # The longest runtime table suffix is 36 bytes. Reserve one underscore and
    # the 12-byte uniqueness token so every table remains within PostgreSQL's
    # 63-byte identifier limit. Long indexes/constraints are separately hashed.
    normalized = normalized[:9].rstrip("_") or "runtime"
    prefix = f"test_{normalized}_{uuid4().hex[:12]}"
    assert_safe_test_prefix(prefix)
    opener = _ACTIVE_SCOPE_OPENER.get()
    if opener is not None:
        opener(prefix)
    active = _ACTIVE_TEST_PREFIXES.get()
    if active is not None:
        active.add(prefix)
    return prefix


@contextmanager
def track_runtime_table_prefixes(
    *,
    scope_opener: Callable[[str], None] | None = None,
) -> Iterator[set[str]]:
    prefixes: set[str] = set()
    token = _ACTIVE_TEST_PREFIXES.set(prefixes)
    opener_token = _ACTIVE_SCOPE_OPENER.set(scope_opener)
    try:
        yield prefixes
    finally:
        _ACTIVE_SCOPE_OPENER.reset(opener_token)
        _ACTIVE_TEST_PREFIXES.reset(token)


def assert_safe_test_prefix(prefix: str) -> None:
    if SAFE_TEST_PREFIX.fullmatch(prefix) is None:
        raise ValueError("refusing to operate on a non-isolated test prefix")


def reset_postgres_availability_cache() -> None:
    _checked_postgres_dsn.cache_clear()


def _runtime_table_names(dsn: str, prefix: str) -> list[str]:
    assert_safe_test_prefix(prefix)
    import psycopg2

    escaped_prefix = prefix.replace("_", r"\_") + r"\_%"
    with psycopg2.connect(dsn, connect_timeout=3) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT tablename FROM pg_tables "
                "WHERE schemaname = current_schema() "
                "AND tablename LIKE %s ESCAPE '\\' "
                "ORDER BY tablename",
                (escaped_prefix,),
            )
            return [str(row[0]) for row in cursor.fetchall()]


def count_runtime_tables(dsn: str, prefix: str) -> int:
    return len(_runtime_table_names(dsn, prefix))


def drop_runtime_tables(dsn: str, prefix: str) -> int:
    """Remove only tables owned by one generated test prefix."""
    assert_safe_test_prefix(prefix)
    import psycopg2
    from psycopg2 import sql

    ownership_table = f"{prefix}_ownership"
    tables = [
        table
        for table in _runtime_table_names(dsn, prefix)
        if table != ownership_table
    ]
    if any(not table.startswith(f"{prefix}_") for table in tables):
        raise RuntimeError("isolated PostgreSQL cleanup escaped its test prefix")
    with psycopg2.connect(dsn) as connection:
        with connection.cursor() as cursor:
            for table_name in reversed(tables):
                cursor.execute(
                    sql.SQL("DROP TABLE IF EXISTS {table} CASCADE").format(
                        table=sql.Identifier(table_name)
                    )
                )
    return len(tables)
