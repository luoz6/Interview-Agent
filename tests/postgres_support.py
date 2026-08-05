from __future__ import annotations

from functools import lru_cache
import os
import re
from uuid import uuid4

import pytest


SAFE_TEST_PREFIX = re.compile(r"^test_[a-z0-9_]+_[0-9a-f]{12}$")


@lru_cache(maxsize=1)
def _checked_postgres_dsn() -> str | None:
    dsn = os.getenv("POSTGRES_DSN")
    if not dsn:
        return None
    try:
        import psycopg2

        with psycopg2.connect(dsn, connect_timeout=3) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                if cursor.fetchone() != (1,):
                    return None
    except Exception:
        return None
    return dsn


def require_postgres_dsn() -> str:
    dsn = _checked_postgres_dsn()
    if dsn is None:
        pytest.skip("configured POSTGRES_DSN is required and must be reachable")
    return dsn


def make_runtime_table_prefix(scope: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", scope.lower()).strip("_")
    # The longest runtime table suffix is 34 bytes. Reserve one underscore and
    # the 12-byte uniqueness token so every table remains within PostgreSQL's
    # 63-byte identifier limit. Long indexes/constraints are separately hashed.
    normalized = normalized[:11].rstrip("_") or "runtime"
    prefix = f"test_{normalized}_{uuid4().hex[:12]}"
    assert_safe_test_prefix(prefix)
    return prefix


def assert_safe_test_prefix(prefix: str) -> None:
    if SAFE_TEST_PREFIX.fullmatch(prefix) is None:
        raise ValueError("refusing to operate on a non-isolated test prefix")


def reset_postgres_availability_cache() -> None:
    _checked_postgres_dsn.cache_clear()
