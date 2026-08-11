from __future__ import annotations


def postgres_sql():
    try:
        from psycopg2 import sql
    except ImportError as exc:
        raise RuntimeError(
            "psycopg2-binary is required for PostgreSQL runtime control"
        ) from exc
    return sql


__all__ = ["postgres_sql"]
