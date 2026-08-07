from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys

import psycopg2
from psycopg2 import sql


SAFE_TEMP_TABLE = re.compile(
    r"^(?:test_[a-z0-9_]+_[0-9a-f]{8,12}(?:_[a-z0-9_]+)?|"
    r"stage38_api_[0-9a-f]{10}_[a-z0-9_]+)$"
)
PROTECTED_TABLES = (
    "checkpoint_blobs",
    "checkpoint_migrations",
    "checkpoint_writes",
    "checkpoints",
)


def is_safe_temporary_table(name: str) -> bool:
    return SAFE_TEMP_TABLE.fullmatch(name) is not None


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _table_inventory(connection) -> tuple[list[str], list[tuple[str, str]]]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT relation.relname, pg_get_userbyid(relation.relowner)
            FROM pg_catalog.pg_class AS relation
            JOIN pg_catalog.pg_namespace AS namespace
              ON namespace.oid = relation.relnamespace
            WHERE namespace.nspname = 'public'
              AND relation.relkind IN ('r', 'p')
            ORDER BY relation.relname
            """
        )
        rows = [(str(name), str(owner)) for name, owner in cursor.fetchall()]
    temporary = [name for name, _owner in rows if is_safe_temporary_table(name)]
    protected = [(name, owner) for name, owner in rows if name in PROTECTED_TABLES]
    unexpected = [
        name
        for name, _owner in rows
        if name not in PROTECTED_TABLES and not is_safe_temporary_table(name)
    ]
    if unexpected:
        raise RuntimeError("unexpected public tables prevent T64 cleanup")
    if [name for name, _owner in protected] != sorted(PROTECTED_TABLES):
        raise RuntimeError("protected PostgreSQL table inventory drifted")
    if any(owner != "postgres" for name, owner in rows if name in temporary):
        raise RuntimeError("temporary PostgreSQL table ownership drifted")
    return temporary, protected


def _relation_residue(connection) -> list[str]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT relation.relname
            FROM pg_catalog.pg_class AS relation
            JOIN pg_catalog.pg_namespace AS namespace
              ON namespace.oid = relation.relnamespace
            WHERE namespace.nspname = 'public'
              AND (
                relation.relname LIKE 'test_%'
                OR relation.relname LIKE 'stage38_api_%'
              )
            ORDER BY relation.relname
            """
        )
        return [str(row[0]) for row in cursor.fetchall()]


def cleanup(*, dsn: str, apply: bool, batch_size: int) -> dict[str, object]:
    if not dsn.strip():
        raise RuntimeError("POSTGRES_DSN is required")
    if not 1 <= batch_size <= 500:
        raise RuntimeError("batch_size must be between 1 and 500")
    with psycopg2.connect(dsn, connect_timeout=5) as connection:
        temporary, protected_before = _table_inventory(connection)
        inventory_sha256 = _canonical_sha256(temporary)
        if apply:
            for offset in range(0, len(temporary), batch_size):
                batch = temporary[offset : offset + batch_size]
                with connection.cursor() as cursor:
                    cursor.execute(
                        sql.SQL("DROP TABLE IF EXISTS {} CASCADE").format(
                            sql.SQL(", ").join(sql.Identifier(name) for name in batch)
                        )
                    )
                connection.commit()
        remaining_tables, protected_after = _table_inventory(connection)
        residue = _relation_residue(connection)
    if protected_after != protected_before:
        raise RuntimeError("protected PostgreSQL table inventory changed")
    if apply and (remaining_tables or residue):
        raise RuntimeError("temporary PostgreSQL relation cleanup is incomplete")
    return {
        "schema_version": "interview-quality-v1-t64-postgres-cleanup-v1",
        "status": "PASS" if apply else "DRY_RUN",
        "applied": apply,
        "temporary_table_count_before": len(temporary),
        "temporary_table_inventory_sha256": inventory_sha256,
        "temporary_table_count_after": len(remaining_tables),
        "temporary_relation_residue": len(residue),
        "protected_tables": [name for name, _owner in protected_after],
        "protected_table_count": len(protected_after),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fail-closed cleanup for T64 isolated PostgreSQL relations"
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    try:
        result = cleanup(
            dsn=os.getenv("POSTGRES_DSN", ""),
            apply=args.apply,
            batch_size=args.batch_size,
        )
    except RuntimeError as exc:
        print(
            json.dumps(
                {
                    "schema_version": "interview-quality-v1-t64-postgres-cleanup-v1",
                    "status": "BLOCKED",
                    "detail": str(exc),
                },
                sort_keys=True,
            )
        )
        return 3
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(rendered, encoding="utf-8")
    sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
