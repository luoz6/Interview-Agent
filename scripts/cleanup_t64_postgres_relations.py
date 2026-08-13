from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Iterator

import psycopg2
from psycopg2 import sql


SAFE_TEMP_TABLE = re.compile(
    r"^(?:test_[a-z0-9_]+_[0-9a-f]{8,12}(?:_[a-z0-9_]+)?|"
    r"stage38_api_[0-9a-f]{10}_[a-z0-9_]+)$"
)
DEDICATED_DATABASE = re.compile(r"^t64_[a-z0-9][a-z0-9_]{0,46}_test$")
CLEANUP_SCHEMA = "interview-quality-v1-t64-postgres-cleanup-v3"
CLEANUP_INVENTORY_SCHEMA = (
    "interview-quality-v1-t64-postgres-cleanup-inventory-v2"
)
PROTECTED_TABLES = (
    "checkpoint_blobs",
    "checkpoint_migrations",
    "checkpoint_writes",
    "checkpoints",
)
_ADVISORY_LOCK_KEYS = (64, 20260808)


def is_safe_temporary_table(name: str) -> bool:
    return SAFE_TEMP_TABLE.fullmatch(name) is not None


def is_dedicated_test_database(name: str) -> bool:
    return DEDICATED_DATABASE.fullmatch(name.strip().casefold()) is not None


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


def _database_identity(connection) -> dict[str, object]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT current_database(), current_user,
                   pg_get_userbyid(database.datdba),
                   database.datistemplate, database.datallowconn
            FROM pg_catalog.pg_database AS database
            WHERE database.datname = current_database()
            """
        )
        row = cursor.fetchone()
    if not row:
        raise RuntimeError("PostgreSQL database identity is unavailable")
    return {
        "database_name": str(row[0]),
        "current_user": str(row[1]),
        "database_owner": str(row[2]),
        "is_template": bool(row[3]),
        "allows_connections": bool(row[4]),
    }


def _validate_database_identity(
    identity: dict[str, object], *, expected_database: str
) -> None:
    if not is_dedicated_test_database(expected_database):
        raise RuntimeError("T64 requires a strictly named dedicated test database")
    if (
        identity.get("database_name") != expected_database
        or identity.get("current_user") != "postgres"
        or identity.get("database_owner") != "postgres"
        or identity.get("is_template") is not False
        or identity.get("allows_connections") is not True
    ):
        raise RuntimeError("T64 dedicated PostgreSQL database identity drifted")


def _relation_inventory(connection) -> list[dict[str, object]]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT relation.relname, relation.oid, relation.relfilenode,
                   pg_get_userbyid(relation.relowner), relation.relkind
            FROM pg_catalog.pg_class AS relation
            JOIN pg_catalog.pg_namespace AS namespace
              ON namespace.oid = relation.relnamespace
            WHERE namespace.nspname = 'public'
              AND relation.relkind IN ('r', 'p')
            ORDER BY relation.relname
            """
        )
        return [
            {
                "name": str(name),
                "oid": int(oid),
                "relfilenode": int(relfilenode),
                "owner": str(owner),
                "relkind": str(relkind),
            }
            for name, oid, relfilenode, owner, relkind in cursor.fetchall()
        ]


def _validate_frozen_baseline(
    inventory: list[dict[str, object]],
) -> None:
    if [item.get("name") for item in inventory] != sorted(PROTECTED_TABLES):
        raise RuntimeError(
            "T64 baseline public schema is not the frozen protected-only set"
        )
    for item in inventory:
        if (
            item.get("owner") != "postgres"
            or item.get("relkind") != "r"
            or not isinstance(item.get("oid"), int)
            or item["oid"] <= 0
            or not isinstance(item.get("relfilenode"), int)
            or item["relfilenode"] <= 0
        ):
            raise RuntimeError("T64 protected relation identity is invalid")


@dataclass
class _T64CleanupAuthority:
    connection: object
    expected_database: str
    database_identity: dict[str, object]
    baseline: list[dict[str, object]]
    active: bool = True


@contextmanager
def t64_cleanup_authority(
    *, dsn: str, expected_database: str
) -> Iterator[_T64CleanupAuthority]:
    if not dsn.strip():
        raise RuntimeError("POSTGRES_DSN is required")
    connection = psycopg2.connect(dsn, connect_timeout=5)
    authority: _T64CleanupAuthority | None = None
    try:
        connection.autocommit = True
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_lock(%s, %s)", _ADVISORY_LOCK_KEYS
            )
        database_identity = _database_identity(connection)
        _validate_database_identity(
            database_identity, expected_database=expected_database
        )
        baseline = _relation_inventory(connection)
        _validate_frozen_baseline(baseline)
        authority = _T64CleanupAuthority(
            connection=connection,
            expected_database=expected_database,
            database_identity=database_identity,
            baseline=baseline,
        )
        yield authority
    finally:
        if authority is not None:
            authority.active = False
        try:
            connection.rollback()
        except Exception:
            pass
        try:
            connection.autocommit = True
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT pg_advisory_unlock(%s, %s)",
                    _ADVISORY_LOCK_KEYS,
                )
        finally:
            connection.close()


def _inventory_by_name(
    inventory: list[dict[str, object]],
) -> dict[str, dict[str, object]]:
    by_name = {str(item["name"]): item for item in inventory}
    if len(by_name) != len(inventory):
        raise RuntimeError("T64 relation inventory contains duplicate names")
    return by_name


def _validate_after_inventory(
    *,
    baseline: list[dict[str, object]],
    current: list[dict[str, object]],
) -> list[dict[str, object]]:
    baseline_by_name = _inventory_by_name(baseline)
    current_by_name = _inventory_by_name(current)
    for name, identity in baseline_by_name.items():
        if current_by_name.get(name) != identity:
            raise RuntimeError("T64 protected relation identity drifted")
    owned = [
        item for item in current if item["name"] not in baseline_by_name
    ]
    for item in owned:
        if (
            not is_safe_temporary_table(str(item["name"]))
            or item.get("owner") != "postgres"
            or item.get("relkind") not in {"r", "p"}
            or not isinstance(item.get("oid"), int)
            or item["oid"] <= 0
            or not isinstance(item.get("relfilenode"), int)
            or item["relfilenode"] < 0
        ):
            raise RuntimeError("T64 discovered a relation outside run ownership")
    return owned


def _lock_relations(
    connection,
    *,
    baseline: list[dict[str, object]],
    owned: list[dict[str, object]],
) -> None:
    with connection.cursor() as cursor:
        if baseline:
            cursor.execute(
                sql.SQL("LOCK TABLE {} IN ACCESS SHARE MODE NOWAIT").format(
                    sql.SQL(", ").join(
                        sql.Identifier("public", str(item["name"]))
                        for item in baseline
                    )
                )
            )
        if owned:
            cursor.execute(
                sql.SQL("LOCK TABLE {} IN ACCESS EXCLUSIVE MODE NOWAIT").format(
                    sql.SQL(", ").join(
                        sql.Identifier("public", str(item["name"]))
                        for item in owned
                    )
                )
            )


def cleanup_with_authority(
    authority: _T64CleanupAuthority,
) -> dict[str, object]:
    if not isinstance(authority, _T64CleanupAuthority) or not authority.active:
        raise RuntimeError("live same-process T64 cleanup authority is required")
    connection = authority.connection
    connection.autocommit = False
    try:
        identity = _database_identity(connection)
        _validate_database_identity(
            identity, expected_database=authority.expected_database
        )
        after = _relation_inventory(connection)
        owned = _validate_after_inventory(
            baseline=authority.baseline, current=after
        )
        _lock_relations(
            connection, baseline=authority.baseline, owned=owned
        )
        locked_inventory = _relation_inventory(connection)
        if locked_inventory != after:
            raise RuntimeError("T64 relation identity changed before cleanup")
        if owned:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL("DROP TABLE {}").format(
                        sql.SQL(", ").join(
                            sql.Identifier("public", str(item["name"]))
                            for item in owned
                        )
                    )
                )
        remaining = _relation_inventory(connection)
        if remaining != authority.baseline:
            raise RuntimeError("T64 cleanup residue or concurrent relation detected")
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.autocommit = True
    return {
        "schema_version": CLEANUP_SCHEMA,
        "status": "PASS",
        "applied": True,
        "authority": "same-process-advisory-lock",
        "database_name": authority.expected_database,
        "dedicated_database_boundary_verified": True,
        "advisory_lock_held": authority.active,
        "baseline_public_table_count": len(authority.baseline),
        "baseline_public_table_inventory_sha256": _canonical_sha256(
            authority.baseline
        ),
        "owned_relation_inventory": owned,
        "owned_temporary_table_count_declared": len(owned),
        "owned_temporary_table_count_after": 0,
        "temporary_relation_residue": 0,
        "protected_tables": list(PROTECTED_TABLES),
        "protected_table_count": len(PROTECTED_TABLES),
        "drop_cascade_used": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="T64 PostgreSQL cleanup has no standalone destructive mode"
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--out", type=Path)
    args, _unknown = parser.parse_known_args(argv)
    blocked = {
        "schema_version": CLEANUP_SCHEMA,
        "status": "BLOCKED",
        "detail": (
            "standalone cleanup is disabled; the T64 runner must hold the "
            "same-process advisory-lock authority"
        ),
        "apply_requested": args.apply,
    }
    rendered = json.dumps(blocked, indent=2, sort_keys=True) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(rendered, encoding="utf-8")
    sys.stdout.write(rendered)
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
