from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Mapping
from uuid import uuid4

from app.services.memory_config import (
    EffectiveMemoryConfig,
    load_effective_memory_config,
)
from app.services.memory_metrics import (
    MemoryMetricDimensions,
    MemoryMetricEvent,
    MemoryMetricValues,
)
from app.services.postgres_memory_metrics import PostgresMemoryMetricStore
from app.services.postgres_schema_contract import RUNTIME_MIGRATIONS
from scripts.memory_postgres_validation import (
    cleanup_isolated_prefix,
    database_fingerprint,
    run_validation,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RC_EVIDENCE = ROOT / "docs" / "memory-validation-operational-evidence.json"
SAFE_STAGING_PREFIX = re.compile(r"^test_memval_[0-9a-f]{12}$")
SAFE_REVISION = re.compile(r"^[0-9a-f]{7,40}$")
SAFE_ROLE = re.compile(r"^[a-z][a-z0-9_-]{2,63}$")


@dataclass(frozen=True)
class StagingDeclaration:
    environment_category: str
    validated_rc_revision: str
    observation_profile: str
    observation_hours: int
    data_category: str
    operator_role: str
    rollback_owner_role: str
    retention_days: int
    backup_restore_scope: str
    isolation_level: str
    co_resident_isolated_staging: bool
    dedicated_connection_scope: bool
    dedicated_worker_scope: bool
    dedicated_owner_scope: bool
    deterministic_path_verified: bool
    allow_real_provider: bool = False


def make_staging_prefix() -> str:
    return f"test_memval_{uuid4().hex[:12]}"


def assert_safe_staging_prefix(prefix: str) -> None:
    if SAFE_STAGING_PREFIX.fullmatch(prefix) is None:
        raise ValueError("refusing to operate on a non-isolated staging prefix")


def validate_declaration(value: StagingDeclaration) -> list[str]:
    failures: list[str] = []
    if value.environment_category != "isolated_staging":
        failures.append("ENVIRONMENT_NOT_ISOLATED_STAGING")
    if SAFE_REVISION.fullmatch(value.validated_rc_revision) is None:
        failures.append("RC_REVISION_INVALID")
    if value.observation_profile not in {"A", "B"}:
        failures.append("OBSERVATION_PROFILE_INVALID")
    if not 1 <= value.observation_hours <= 24 * 31:
        failures.append("OBSERVATION_WINDOW_INVALID")
    if value.observation_profile == "A" and value.observation_hours < 168:
        failures.append("PROFILE_A_WINDOW_TOO_SHORT")
    if value.data_category not in {"synthetic", "internal_authorized"}:
        failures.append("DATA_CATEGORY_NOT_APPROVED")
    if SAFE_ROLE.fullmatch(value.operator_role) is None:
        failures.append("OPERATOR_ROLE_INVALID")
    if SAFE_ROLE.fullmatch(value.rollback_owner_role) is None:
        failures.append("ROLLBACK_OWNER_ROLE_INVALID")
    if not 1 <= value.retention_days <= 30:
        failures.append("RETENTION_WINDOW_INVALID")
    if value.backup_restore_scope != "isolated_copy":
        failures.append("BACKUP_RESTORE_SCOPE_NOT_ISOLATED")
    if value.isolation_level not in {"instance", "database", "strict_prefix"}:
        failures.append("ISOLATION_LEVEL_INVALID")
    if value.allow_real_provider:
        failures.append("REAL_PROVIDER_NOT_AUTHORIZED")
    if not value.deterministic_path_verified:
        failures.append("DETERMINISTIC_PATH_NOT_VERIFIED")
    if value.isolation_level == "strict_prefix":
        if not value.co_resident_isolated_staging:
            failures.append("CO_RESIDENT_STAGING_NOT_DECLARED")
        if not value.dedicated_connection_scope:
            failures.append("CO_RESIDENT_CONNECTION_SCOPE_NOT_ISOLATED")
        if not value.dedicated_worker_scope:
            failures.append("CO_RESIDENT_WORKER_SCOPE_NOT_ISOLATED")
        if not value.dedicated_owner_scope:
            failures.append("CO_RESIDENT_OWNER_SCOPE_NOT_ISOLATED")
    return sorted(set(failures))


def validate_rc_evidence(
    declaration: StagingDeclaration,
    evidence: Mapping[str, object],
) -> list[str]:
    failures: list[str] = []
    if evidence.get("validated_rc_revision") != declaration.validated_rc_revision:
        failures.append("RC_REVISION_MISMATCH")
    release = evidence.get("release_candidate")
    if not isinstance(release, Mapping) or not bool(release.get("passed")):
        failures.append("RC_REPRODUCIBILITY_NOT_PROVEN")
    else:
        if not bool(release.get("clean_detached_worktree")):
            failures.append("RC_CLEAN_CHECKOUT_NOT_PROVEN")
        if bool(release.get("shadow_modes_changed")):
            failures.append("RC_VALIDATION_CHANGED_SHADOW_MODES")
    required_green = (
        "full_python",
        "pg_runtime",
        "frontend_build",
        "full_browser",
        "cleanup",
    )
    for key in required_green:
        value = evidence.get(key)
        if not isinstance(value, Mapping) or not bool(value.get("passed")):
            failures.append(f"RC_{key.upper()}_NOT_GREEN")
    pg_runtime = evidence.get("pg_runtime")
    if isinstance(pg_runtime, Mapping) and int(pg_runtime.get("executed", 0)) < 1:
        failures.append("RC_POSTGRES_TESTS_NOT_EXECUTED")
    browser = evidence.get("full_browser")
    if isinstance(browser, Mapping) and browser.get("scope") != "full":
        failures.append("RC_BROWSER_SCOPE_NOT_FULL")
    cleanup = evidence.get("cleanup")
    if isinstance(cleanup, Mapping):
        if int(cleanup.get("test_listeners", -1)) != 0:
            failures.append("RC_TEST_LISTENER_RESIDUE")
        if int(cleanup.get("isolated_test_relation_residue", -1)) != 0:
            failures.append("RC_POSTGRES_RELATION_RESIDUE")
    if evidence.get("production_observation") != "NOT_RUN":
        failures.append("PRODUCTION_OBSERVATION_STATE_INVALID")
    return sorted(set(failures))


def validate_disabled_config(config: EffectiveMemoryConfig) -> list[str]:
    failures: list[str] = []
    if config.budget.mode != "disabled":
        failures.append("BUDGET_MODE_NOT_DISABLED")
    if config.budget.shadow_enabled:
        failures.append("BUDGET_SHADOW_GATE_ENABLED")
    if any(config.budget.enforcement.model_dump().values()):
        failures.append("BUDGET_ENFORCEMENT_ENABLED")
    if config.compression.mode != "disabled":
        failures.append("COMPRESSION_MODE_NOT_DISABLED")
    if any(
        (
            config.compression.interview_question_memory,
            config.compression.evidence,
            config.compression.prep,
            config.compression.review,
        )
    ):
        failures.append("QUESTION_OR_CONTEXT_MEMORY_CONSUMPTION_ENABLED")
    if config.long_term.mode != "disabled":
        failures.append("LONG_TERM_MODE_NOT_DISABLED")
    if config.long_term.write_shadow_enabled:
        failures.append("WRITE_SHADOW_GATE_ENABLED")
    if config.long_term.read_shadow_enabled:
        failures.append("READ_SHADOW_GATE_ENABLED")
    if config.long_term.trusted_local_api_enabled:
        failures.append("TRUSTED_LOCAL_PRINCIPAL_API_ENABLED")
    return sorted(set(failures))


def verify_consume_rejected(environ: Mapping[str, str]) -> bool:
    candidate = dict(environ)
    candidate["MEMORY_LONG_TERM_MODE"] = "consume"
    try:
        load_effective_memory_config(candidate)
    except ValueError as exc:
        return "consume is not supported" in str(exc)
    return False


def _configuration_digest(
    declaration: StagingDeclaration,
    config: EffectiveMemoryConfig,
) -> str:
    payload = {
        "declaration": asdict(declaration),
        "memory": {
            "budget_mode": config.budget.mode,
            "compression_mode": config.compression.mode,
            "long_term_mode": config.long_term.mode,
            "write_shadow_enabled": config.long_term.write_shadow_enabled,
            "read_shadow_enabled": config.long_term.read_shadow_enabled,
            "trusted_local_api_enabled": config.long_term.trusted_local_api_enabled,
        },
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def evaluate_preflight(
    *,
    declaration: StagingDeclaration,
    rc_evidence: Mapping[str, object],
    config: EffectiveMemoryConfig,
    consume_rejected: bool,
    database_fingerprint_matches: bool,
    prefix_valid: bool,
    migration_validated: bool,
    durable_metrics_validated: bool,
    rollback_verified: bool,
    cleanup_residue: int,
) -> dict:
    failures = validate_declaration(declaration)
    failures.extend(validate_rc_evidence(declaration, rc_evidence))
    failures.extend(validate_disabled_config(config))
    if not consume_rejected:
        failures.append("CONSUME_REJECTION_NOT_PROVEN")
    if not database_fingerprint_matches:
        failures.append("DATABASE_FINGERPRINT_MISMATCH")
    if not prefix_valid:
        failures.append("STAGING_PREFIX_INVALID")
    if not migration_validated:
        failures.append("MIGRATION_VALIDATION_FAILED")
    if not durable_metrics_validated:
        failures.append("DURABLE_METRICS_VALIDATION_FAILED")
    if not rollback_verified:
        failures.append("ROLLBACK_DRILL_FAILED")
    if cleanup_residue != 0:
        failures.append("CLEANUP_RESIDUE_NONZERO")
    failures = sorted(set(failures))
    return {
        "schema_version": "memory-shadow-staging-preflight-v1",
        "mode": "EXECUTE",
        "passed": not failures,
        "gate_codes": failures,
        "validated_rc_revision": declaration.validated_rc_revision,
        "environment_category": declaration.environment_category,
        "observation_profile": declaration.observation_profile,
        "observation_hours": declaration.observation_hours,
        "data_category": declaration.data_category,
        "retention_days": declaration.retention_days,
        "isolation_level": declaration.isolation_level,
        "co_resident_isolated_staging": (
            declaration.co_resident_isolated_staging
        ),
        "configuration_digest": _configuration_digest(declaration, config),
        "configuration_changed": False,
        "all_memory_shadows_disabled": not validate_disabled_config(config),
        "real_provider_allowed": False,
        "migration_scope": "isolated",
        "database_fingerprint_matches": database_fingerprint_matches,
        "prefix_valid": prefix_valid,
        "migration_validated": migration_validated,
        "durable_metrics_validated": durable_metrics_validated,
        "rollback_verified": rollback_verified,
        "cleanup_residue": cleanup_residue,
        "worker_leasing_started": False,
        "production_observation": "NOT_RUN",
        "long_term_memory_consumption": "BLOCKED",
    }


def count_isolated_relations(dsn: str, prefix: str) -> int:
    assert_safe_staging_prefix(prefix)
    import psycopg2

    with psycopg2.connect(dsn, connect_timeout=3) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) FROM pg_tables "
                "WHERE schemaname = 'public' AND tablename LIKE %s",
                (f"{prefix}_%",),
            )
            row = cursor.fetchone()
    return int(row[0]) if row else 0


def _validate_durable_metrics(dsn: str, prefix: str) -> bool:
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    store = PostgresMemoryMetricStore(
        dsn=dsn,
        table_prefix=prefix,
        schema_mode="validate",
        clock=lambda: now,
        minimum_language_samples=1,
    )
    store.publish(
        MemoryMetricEvent(
            metric_code="provider_usage",
            dimensions=MemoryMetricDimensions(
                operation="provider",
                language_bucket="zh_hans",
                shadow_mode=False,
                consumption_enabled=False,
            ),
            values=MemoryMetricValues(provider_input_tokens=1),
            observed_at=now - timedelta(minutes=65),
        )
    )
    aggregate = store.aggregate(window_minutes=1440)
    rollup_count = store.rollup(batch_size=100)
    cleanup = store.cleanup(
        minute_retention_days=1,
        hour_retention_days=1,
        batch_size=100,
    )
    return bool(
        aggregate.get("store_kind") == "postgres_aggregate"
        and aggregate.get("data_complete") is True
        and aggregate.get("items")
        and rollup_count >= 1
        and set(cleanup) == {"minute_deleted", "hour_deleted"}
    )


def run_live_preflight(
    *,
    declaration: StagingDeclaration,
    rc_evidence: Mapping[str, object],
    environ: Mapping[str, str],
    dsn: str,
    table_prefix: str,
    expected_database_fingerprint: str,
) -> dict:
    config = load_effective_memory_config(environ)
    consume_rejected = verify_consume_rejected(environ)
    prefix_valid = True
    try:
        assert_safe_staging_prefix(table_prefix)
    except ValueError:
        prefix_valid = False
    actual_fingerprint = database_fingerprint(dsn).digest
    fingerprint_matches = actual_fingerprint == expected_database_fingerprint
    migration_validated = False
    durable_metrics_validated = False
    rollback_verified = False
    cleanup_residue = count_isolated_relations(dsn, table_prefix) if prefix_valid else -1

    static_failures = (
        validate_declaration(declaration)
        + validate_rc_evidence(declaration, rc_evidence)
        + validate_disabled_config(config)
    )
    if not consume_rejected:
        static_failures.append("CONSUME_REJECTION_NOT_PROVEN")
    if not fingerprint_matches:
        static_failures.append("DATABASE_FINGERPRINT_MISMATCH")
    if not prefix_valid:
        static_failures.append("STAGING_PREFIX_INVALID")

    try:
        if not static_failures:
            validation = run_validation(
                dsn=dsn,
                table_prefix=table_prefix,
                keep_tables=True,
            )
            required = {spec.migration_id for spec in RUNTIME_MIGRATIONS}
            migration_validated = bool(
                validation.relation_count > 0
                and required.issubset(validation.required_migration_ids)
            )
            durable_metrics_validated = _validate_durable_metrics(
                dsn,
                table_prefix,
            )
    finally:
        if prefix_valid:
            cleanup_isolated_prefix(dsn, table_prefix)
            cleanup_residue = count_isolated_relations(dsn, table_prefix)
            rollback_verified = cleanup_residue == 0

    return evaluate_preflight(
        declaration=declaration,
        rc_evidence=rc_evidence,
        config=config,
        consume_rejected=consume_rejected,
        database_fingerprint_matches=fingerprint_matches,
        prefix_valid=prefix_valid,
        migration_validated=migration_validated,
        durable_metrics_validated=durable_metrics_validated,
        rollback_verified=rollback_verified,
        cleanup_residue=cleanup_residue,
    )


def _load_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("RC evidence must be a JSON object")
    return value


def _declaration_from_args(args: argparse.Namespace) -> StagingDeclaration:
    return StagingDeclaration(
        environment_category="isolated_staging",
        validated_rc_revision=args.validated_rc_revision,
        observation_profile=args.observation_profile,
        observation_hours=args.observation_hours,
        data_category=args.data_category,
        operator_role=args.operator_role,
        rollback_owner_role=args.rollback_owner_role,
        retention_days=args.retention_days,
        backup_restore_scope="isolated_copy",
        isolation_level=args.isolation_level,
        co_resident_isolated_staging=args.co_resident_isolated_staging,
        dedicated_connection_scope=args.dedicated_connection_scope,
        dedicated_worker_scope=args.dedicated_worker_scope,
        dedicated_owner_scope=args.dedicated_owner_scope,
        deterministic_path_verified=args.deterministic_path_verified,
        allow_real_provider=args.allow_real_provider,
    )


def _dry_run_result(
    declaration: StagingDeclaration,
    rc_evidence: Mapping[str, object],
    config: EffectiveMemoryConfig,
    consume_rejected: bool,
) -> dict:
    failures = validate_declaration(declaration)
    failures.extend(validate_rc_evidence(declaration, rc_evidence))
    failures.extend(validate_disabled_config(config))
    if not consume_rejected:
        failures.append("CONSUME_REJECTION_NOT_PROVEN")
    failures = sorted(set(failures))
    return {
        "schema_version": "memory-shadow-staging-preflight-v1",
        "mode": "DRY_RUN",
        "static_checks_passed": not failures,
        "passed": False,
        "gate_codes": failures or ["LIVE_VALIDATION_NOT_RUN"],
        "validated_rc_revision": declaration.validated_rc_revision,
        "configuration_digest": _configuration_digest(declaration, config),
        "configuration_changed": False,
        "all_memory_shadows_disabled": not validate_disabled_config(config),
        "real_provider_allowed": False,
        "migration_scope": "NOT_RUN",
        "production_observation": "NOT_RUN",
        "long_term_memory_consumption": "BLOCKED",
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate an isolated Memory Shadow Staging target."
    )
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--inspect-database-fingerprint", action="store_true")
    parser.add_argument(
        "--rc-evidence",
        type=Path,
        default=DEFAULT_RC_EVIDENCE,
    )
    parser.add_argument("--validated-rc-revision")
    parser.add_argument("--observation-profile", choices=("A", "B"))
    parser.add_argument("--observation-hours", type=int)
    parser.add_argument(
        "--data-category",
        choices=("synthetic", "internal_authorized"),
        default="synthetic",
    )
    parser.add_argument("--operator-role", default="memory-shadow-operator")
    parser.add_argument(
        "--rollback-owner-role",
        default="memory-shadow-rollback-owner",
    )
    parser.add_argument("--retention-days", type=int, default=7)
    parser.add_argument(
        "--isolation-level",
        choices=("instance", "database", "strict_prefix"),
        default="strict_prefix",
    )
    parser.add_argument("--co-resident-isolated-staging", action="store_true")
    parser.add_argument("--dedicated-connection-scope", action="store_true")
    parser.add_argument("--dedicated-worker-scope", action="store_true")
    parser.add_argument("--dedicated-owner-scope", action="store_true")
    parser.add_argument("--deterministic-path-verified", action="store_true")
    parser.add_argument("--allow-real-provider", action="store_true")
    parser.add_argument("--expected-database-fingerprint")
    parser.add_argument("--table-prefix")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.inspect_database_fingerprint:
        dsn = os.getenv("POSTGRES_DSN", "").strip()
        if not dsn:
            raise RuntimeError(
                "POSTGRES_DSN is required for --inspect-database-fingerprint"
            )
        fingerprint = database_fingerprint(dsn)
        print(
            json.dumps(
                {
                    "schema_version": "memory-shadow-database-fingerprint-v1",
                    "database_fingerprint": fingerprint.digest,
                    "dsn_redacted": True,
                    "production_like_database_rejected": True,
                },
                sort_keys=True,
            )
        )
        return 0
    if not args.validated_rc_revision:
        parser.error("--validated-rc-revision is required")
    if not args.observation_profile:
        parser.error("--observation-profile is required")
    if args.observation_hours is None:
        parser.error("--observation-hours is required")
    rc_evidence = _load_json(args.rc_evidence)
    declaration = _declaration_from_args(args)
    config = load_effective_memory_config(os.environ)
    consume_rejected = verify_consume_rejected(os.environ)
    if not args.execute:
        result = _dry_run_result(
            declaration,
            rc_evidence,
            config,
            consume_rejected,
        )
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0 if result["static_checks_passed"] else 1

    dsn = os.getenv("POSTGRES_DSN", "").strip()
    if not dsn:
        raise RuntimeError("POSTGRES_DSN is required for --execute")
    if not args.expected_database_fingerprint:
        raise RuntimeError(
            "--expected-database-fingerprint is required for --execute"
        )
    result = run_live_preflight(
        declaration=declaration,
        rc_evidence=rc_evidence,
        environ=os.environ,
        dsn=dsn,
        table_prefix=args.table_prefix or make_staging_prefix(),
        expected_database_fingerprint=args.expected_database_fingerprint,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
