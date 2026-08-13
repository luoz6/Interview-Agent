from __future__ import annotations

import argparse
import json
import os
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.ports.postgres_scope import PostgresScopeApproval
from app.runtime.config.compatibility import derive_pgvector_table_names


EXPECTED_CORPUS_VERSION = "memory-p1-zh-v4"
EXPECTED_CORPUS_HASH = (
    "deb709817c6ea1ac89db8f0452f1183d0168952d5d568e08b704869c90555e84"
)
APPROVAL_NAMES = (
    "POSTGRES_TEST_APPROVAL_ID",
    "POSTGRES_TEST_APPROVAL_RECEIPT_SHA256",
    "POSTGRES_TEST_APPROVED_FINGERPRINT",
    "POSTGRES_TEST_DATABASE_ALLOWLIST",
    "POSTGRES_TEST_APPROVAL_EXPIRES_AT",
)
OPERATOR_FLAG = "RUN_KNOWLEDGE_ROCKETMQ_V4_LOAD"
SAFE_PREFLIGHT_SCOPE = "test_rmqv4_000000000000"


def _target_identity(cursor) -> dict[str, Any]:
    cursor.execute("SELECT system_identifier::text FROM pg_control_system()")
    system_row = cursor.fetchone()
    if system_row is None or not str(system_row[0]).strip():
        raise RuntimeError("PostgreSQL system identifier is unavailable")
    cursor.execute(
        "SELECT current_database(), "
        "(SELECT oid::bigint FROM pg_database WHERE datname=current_database()), "
        "current_setting('server_version_num')::int, "
        "COALESCE(inet_server_addr()::text, 'local-socket'), "
        "COALESCE(inet_server_port(), 0), current_user, current_schema(), "
        "current_setting('transaction_read_only')"
    )
    row = cursor.fetchone()
    if row is None:
        raise RuntimeError("PostgreSQL target identity query returned no row")
    from app.ports.postgres_scope import PostgresTargetIdentity

    identity = PostgresTargetIdentity(
        system_identifier=str(system_row[0]),
        database_name=str(row[0]),
        database_oid=int(row[1]),
        server_version_num=int(row[2]),
        server_address=str(row[3]),
        server_port=int(row[4]),
        current_user=str(row[5]),
        current_schema=str(row[6]),
    )
    return {
        "database_name": identity.database_name,
        "database_oid": identity.database_oid,
        "server_version_num": identity.server_version_num,
        "server_address": identity.server_address,
        "server_port": identity.server_port,
        "current_user": identity.current_user,
        "current_schema": identity.current_schema,
        "transaction_read_only": str(row[7]),
        "fingerprint": identity.fingerprint,
    }


def inspect_postgres_target(
    *,
    dsn: str,
    table_name: str,
    connect_timeout: int = 3,
) -> dict[str, Any]:
    """Inspect the configured target in a read-only transaction."""

    import psycopg2
    from psycopg2 import sql

    versions_table, releases_table = derive_pgvector_table_names(table_name)
    connection = psycopg2.connect(
        dsn,
        connect_timeout=connect_timeout,
        options=(
            "-c default_transaction_read_only=on "
            "-c statement_timeout=5000"
        ),
    )
    try:
        connection.set_session(readonly=True, autocommit=False)
        with connection.cursor() as cursor:
            identity = _target_identity(cursor)
            cursor.execute(
                "SELECT extversion FROM pg_extension WHERE extname='vector'"
            )
            extension_row = cursor.fetchone()
            cursor.execute(
                "SELECT to_regclass(%s)::text, to_regclass(%s)::text, "
                "to_regclass(%s)::text",
                (table_name, versions_table, releases_table),
            )
            relation_row = cursor.fetchone()
            if relation_row is None:
                relation_row = (None, None, None)
            releases: list[dict[str, Any]] = []
            version_row_counts: dict[str, int] = {}
            if relation_row[2] is not None:
                cursor.execute(
                    sql.SQL(
                        "SELECT corpus_version, manifest_sha256, "
                        "embedding_provider, embedding_model, "
                        "embedding_revision, embedding_dimension, "
                        "chunk_count, status FROM {table} ORDER BY created_at"
                    ).format(table=sql.Identifier(releases_table))
                )
                releases = [
                    {
                        "corpus_version": str(row[0]),
                        "manifest_sha256": str(row[1]),
                        "provider": str(row[2]),
                        "model": str(row[3]),
                        "revision": str(row[4]),
                        "dimension": int(row[5]),
                        "chunk_count": int(row[6]),
                        "status": str(row[7]),
                    }
                    for row in cursor.fetchall()
                ]
            if relation_row[1] is not None:
                cursor.execute(
                    sql.SQL(
                        "SELECT corpus_version, count(*) FROM {table} "
                        "GROUP BY corpus_version ORDER BY corpus_version"
                    ).format(table=sql.Identifier(versions_table))
                )
                version_row_counts = {
                    str(row[0]): int(row[1]) for row in cursor.fetchall()
                }
    finally:
        connection.rollback()
        connection.close()

    active_releases = [item for item in releases if item["status"] == "active"]
    return {
        "reachable": True,
        "identity": identity,
        "vector_extension_version": (
            str(extension_row[0]) if extension_row is not None else None
        ),
        "tables": {
            "configured": table_name,
            "legacy_exists": relation_row[0] is not None,
            "versions": versions_table,
            "versions_exists": relation_row[1] is not None,
            "releases": releases_table,
            "releases_exists": relation_row[2] is not None,
        },
        "releases": releases,
        "version_row_counts": version_row_counts,
        "active_corpus_version": (
            active_releases[0]["corpus_version"] if active_releases else None
        ),
        "active_manifest_sha256": (
            active_releases[0]["manifest_sha256"] if active_releases else None
        ),
    }


def _approval_status(
    target: dict[str, Any],
    environ: Mapping[str, str],
    *,
    now: datetime,
) -> tuple[bool, list[str]]:
    missing = [name for name in APPROVAL_NAMES if not environ.get(name, "").strip()]
    if missing:
        return False, missing
    try:
        expires_at = datetime.fromisoformat(
            environ["POSTGRES_TEST_APPROVAL_EXPIRES_AT"].replace("Z", "+00:00")
        )
        approval = PostgresScopeApproval(
            approval_id=environ["POSTGRES_TEST_APPROVAL_ID"],
            approval_receipt_sha256=environ[
                "POSTGRES_TEST_APPROVAL_RECEIPT_SHA256"
            ],
            approved_target_fingerprint=environ[
                "POSTGRES_TEST_APPROVED_FINGERPRINT"
            ],
            database_allowlist=frozenset(
                item.strip()
                for item in environ["POSTGRES_TEST_DATABASE_ALLOWLIST"].split(",")
                if item.strip()
            ),
            scope_prefix=SAFE_PREFLIGHT_SCOPE,
            expires_at=expires_at,
        )
        approval.validate_static(now)
    except (KeyError, ValueError, TypeError, RuntimeError):
        return False, ["POSTGRES_TEST_APPROVAL_INVALID"]
    identity = target["identity"]
    if identity["database_name"] not in approval.database_allowlist:
        return False, ["POSTGRES_TEST_DATABASE_OUTSIDE_ALLOWLIST"]
    if identity["fingerprint"] != approval.approved_target_fingerprint:
        return False, ["POSTGRES_TEST_TARGET_FINGERPRINT_MISMATCH"]
    return True, []


def evaluate_target_preflight(
    target: dict[str, Any],
    environ: Mapping[str, str],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    failure_reasons: list[str] = []
    current_time = now or datetime.now(timezone.utc)
    approval_valid, approval_missing = _approval_status(
        target,
        environ,
        now=current_time,
    )
    if not approval_valid:
        failure_reasons.append("POSTGRES_SCOPE_APPROVAL_REQUIRED")

    provider = environ.get("EMBEDDING_PROVIDER", "").strip().lower()
    model_name = environ.get("EMBEDDING_MODEL_NAME", "").strip()
    model_revision = environ.get("EMBEDDING_MODEL_REVISION", "").strip()
    api_key_present = bool(environ.get("SILICONFLOW_API_KEY", "").strip())
    embedding_fixed = (
        provider == "siliconflow"
        and bool(model_name)
        and bool(model_revision)
        and model_revision != "siliconflow-current"
        and api_key_present
    )
    if not embedding_fixed:
        failure_reasons.append("FIXED_EMBEDDING_IDENTITY_REQUIRED")

    operator_authorized = environ.get(OPERATOR_FLAG, "").strip() == "1"
    if not operator_authorized:
        failure_reasons.append("ROCKETMQ_V4_LOAD_AUTHORIZATION_REQUIRED")

    tables = target.get("tables", {})
    target_compatible = (
        target.get("reachable") is True
        and bool(target.get("vector_extension_version"))
        and target.get("identity", {}).get("transaction_read_only") == "on"
        and tables.get("versions_exists") is True
        and tables.get("releases_exists") is True
    )
    if not target_compatible:
        failure_reasons.append("PGVECTOR_TARGET_NOT_READY")

    active_version = target.get("active_corpus_version")
    active_hash = target.get("active_manifest_sha256")
    active_is_expected = (
        active_version == EXPECTED_CORPUS_VERSION
        and active_hash == EXPECTED_CORPUS_HASH
    )
    activation_would_replace_existing = (
        active_version is not None and not active_is_expected
    )
    if activation_would_replace_existing:
        failure_reasons.append("ACTIVE_CORPUS_REPLACEMENT_REQUIRES_REVIEW")

    return {
        "schema_version": "knowledge-rocketmq-v4-target-preflight-v1",
        "passed": not failure_reasons,
        "write_ready": not failure_reasons,
        "failure_reasons": failure_reasons,
        "target": target,
        "approval": {
            "valid": approval_valid,
            "missing_or_invalid": approval_missing,
        },
        "embedding": {
            "fixed_identity": embedding_fixed,
            "provider": provider or None,
            "model_name": model_name or None,
            "model_revision": model_revision or None,
            "api_key_present": api_key_present,
        },
        "operator_authorized": operator_authorized,
        "expected_corpus": {
            "version": EXPECTED_CORPUS_VERSION,
            "manifest_sha256": EXPECTED_CORPUS_HASH,
        },
        "active_is_expected": active_is_expected,
        "activation_would_replace_existing": activation_would_replace_existing,
    }


def build_target_preflight(
    *,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    resolved = dict(os.environ if environ is None else environ)
    dsn = resolved.get("POSTGRES_DSN", "").strip()
    if not dsn:
        return {
            "schema_version": "knowledge-rocketmq-v4-target-preflight-v1",
            "passed": False,
            "write_ready": False,
            "failure_reasons": ["POSTGRES_DSN_REQUIRED"],
        }
    table_name = resolved.get("PGVECTOR_TABLE", "knowledge_chunks").strip()
    try:
        target = inspect_postgres_target(dsn=dsn, table_name=table_name)
    except Exception as exc:
        return {
            "schema_version": "knowledge-rocketmq-v4-target-preflight-v1",
            "passed": False,
            "write_ready": False,
            "failure_reasons": ["POSTGRES_TARGET_UNREACHABLE"],
            "error_type": type(exc).__name__,
        }
    return evaluate_target_preflight(target, resolved)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Inspect the RocketMQ V4 pgvector target without writes"
    )
    parser.parse_args(argv)
    result = build_target_preflight()
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
