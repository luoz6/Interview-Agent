from __future__ import annotations

from typing import Any


def postgres_sql():
    try:
        from psycopg2 import sql
    except ImportError as exc:
        raise RuntimeError("psycopg2-binary is required") from exc
    return sql


def iso_timestamp(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return value.isoformat().replace("+00:00", "Z")


__all__ = ["iso_timestamp", "postgres_sql"]
