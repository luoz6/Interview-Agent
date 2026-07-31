from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
from typing import Mapping

from app.services.memory_config import load_effective_memory_config


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REGRESSION = ROOT / "docs" / "memory-operational-regression-evidence.json"
DEFAULT_OUTPUT = ROOT / "docs" / "memory-operational-shadow-evidence.json"
SUCCESS_LINES = (
    "MEMORY_SHADOW_RC=REPRODUCIBLE",
    "BUDGET_SHADOW_STAGING=PASS",
    "PRINCIPAL_WRITE_SHADOW_STAGING=PASS",
    "PRINCIPAL_READ_SHADOW_ZERO_INJECTION_STAGING=PASS",
    "CONSENT_DELETION_RESTORE_DRILL=PASS",
    "PRODUCTION_SHADOW_APPROVAL_REQUIRED",
    "LONG_TERM_MEMORY_CONSUMPTION=BLOCKED",
    "PRODUCTION_OBSERVATION=NOT_RUN",
)
_PRIVATE_KEYS = frozenset(
    {
        "session_id",
        "principal_id",
        "fact_id",
        "question_id",
        "message_id",
        "normalized_fact",
        "source_excerpt",
        "source_manifest_sha256",
        "artifact_ref",
        "provider_payload",
        "prompt",
        "answer",
        "resume",
        "dsn",
        "database_fingerprint",
        "table_prefix",
    }
)


class AcceptanceBlocked(RuntimeError):
    def __init__(self, codes) -> None:
        self.codes = tuple(sorted(set(codes)))
        super().__init__("operational Memory Shadow acceptance blocked")


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _integer(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _all_zero(value: object) -> bool:
    return all(_integer(item) == 0 for item in _mapping(value).values())


def _passed_section(value: object) -> bool:
    section = _mapping(value)
    return bool(section.get("passed")) and _integer(section.get("failed")) == 0


def evaluate_operational_shadow(
    bundle: Mapping[str, object]
) -> tuple[str, ...]:
    codes: list[str] = []
    rc = _mapping(bundle.get("rc"))
    release = _mapping(rc.get("release_candidate"))
    if not release.get("passed") or not release.get("clean_detached_worktree"):
        codes.append("RC_NOT_REPRODUCIBLE")
    for key, code in (
        ("full_python", "RC_FULL_PYTHON_NOT_GREEN"),
        ("pg_runtime", "RC_POSTGRES_NOT_GREEN"),
        ("frontend_build", "RC_FRONTEND_NOT_GREEN"),
        ("full_browser", "RC_BROWSER_NOT_GREEN"),
    ):
        if not _passed_section(rc.get(key)):
            codes.append(code)
    rc_browser = _mapping(rc.get("full_browser"))
    if rc_browser.get("scope") != "full":
        codes.append("RC_BROWSER_SCOPE_PARTIAL")
    rc_metrics = _mapping(rc.get("durable_metrics"))
    if (
        not rc_metrics.get("passed")
        or rc_metrics.get("store_kind") != "postgres_aggregate"
        or not rc_metrics.get("data_complete")
    ):
        codes.append("RC_DURABLE_METRICS_INCOMPLETE")

    regression = _mapping(bundle.get("regression"))
    if not regression:
        codes.append("REGRESSION_EVIDENCE_MISSING")
    if not regression.get("clean_detached_worktree"):
        codes.append("REGRESSION_NOT_CLEAN_REVISION")
    for key, code in (
        ("full_python", "FULL_PYTHON_REGRESSION_FAILED"),
        ("pg_runtime", "POSTGRES_REGRESSION_FAILED"),
        ("frontend_build", "FRONTEND_BUILD_REGRESSION_FAILED"),
        ("full_browser", "BROWSER_REGRESSION_FAILED"),
        ("compileall", "COMPILEALL_FAILED"),
        ("diff_check", "DIFF_CHECK_FAILED"),
    ):
        if not _passed_section(regression.get(key)):
            codes.append(code)
    regression_browser = _mapping(regression.get("full_browser"))
    if regression_browser.get("scope") != "full":
        codes.append("BROWSER_SCOPE_PARTIAL")
    cleanup = _mapping(regression.get("cleanup"))
    if _integer(cleanup.get("test_listeners"), -1) != 0:
        codes.append("TEST_LISTENER_RESIDUE")
    if _integer(cleanup.get("isolated_test_relation_residue"), -1) != 0:
        codes.append("POSTGRES_RELATION_RESIDUE")

    staging_text = str(bundle.get("staging_text", ""))
    for line, code in (
        ("STAGING_PREFLIGHT=PASS", "STAGING_PREFLIGHT_FAILED"),
        ("MIGRATION_SCOPE=ISOLATED", "STAGING_MIGRATION_NOT_ISOLATED"),
        ("ROLLBACK_DRILL=PASS", "STAGING_ROLLBACK_FAILED"),
    ):
        if line not in staging_text:
            codes.append(code)

    budget_text = str(bundle.get("budget_text", ""))
    if "BUDGET_SHADOW_STAGING=PASS" not in budget_text:
        codes.append("BUDGET_SHADOW_NOT_ACCEPTED")
    if "BUDGET_ENFORCEMENT=BLOCKED" not in budget_text:
        codes.append("BUDGET_ENFORCEMENT_NOT_BLOCKED")
    budget = _mapping(bundle.get("budget"))
    if budget.get("profile") != "B" or _integer(
        budget.get("followup_sample_count")
    ) < 300:
        codes.append("BUDGET_SAMPLE_PROFILE_INCOMPLETE")
    languages = _mapping(budget.get("language_sample_counts"))
    if any(_integer(languages.get(key)) < 100 for key in ("en", "mixed", "zh_hans")):
        codes.append("BUDGET_LANGUAGE_MATRIX_INCOMPLETE")
    if not budget.get("data_complete"):
        codes.append("BUDGET_METRICS_INCOMPLETE")
    if _integer(budget.get("mandatory_current_content_losses")):
        codes.append("BUDGET_MANDATORY_CONTENT_LOSS")
    if _integer(budget.get("known_over_budget_provider_calls")):
        codes.append("BUDGET_OVER_LIMIT_PROVIDER_CALL")
    if _integer(budget.get("cleanup_residue")) or not budget.get(
        "rollback_verified"
    ):
        codes.append("BUDGET_CLEANUP_OR_ROLLBACK_FAILED")

    write = _mapping(bundle.get("write"))
    if _integer(write.get("sample_count")) < 300:
        codes.append("PRINCIPAL_WRITE_SAMPLE_INCOMPLETE")
    if not _all_zero(write.get("hard_invariants")):
        codes.append("PRINCIPAL_WRITE_HARD_INVARIANT")
    if _integer(write.get("cleanup_residue")) or not write.get(
        "rollback_verified"
    ):
        codes.append("PRINCIPAL_WRITE_CLEANUP_OR_ROLLBACK_FAILED")

    quality = _mapping(bundle.get("quality"))
    if quality.get("quality_gate") != "PASS" or _integer(
        quality.get("reviewed_count")
    ) < 300:
        codes.append("PROPOSAL_QUALITY_FAILED")
    if _integer(quality.get("privacy_sensitive_count")):
        codes.append("PROPOSAL_PRIVACY_SENSITIVE")
    if _integer(quality.get("stale_source_accepted_count")):
        codes.append("PROPOSAL_STALE_SOURCE_ACCEPTED")

    read = _mapping(bundle.get("read"))
    if _integer(read.get("sample_count")) < 300:
        codes.append("PRINCIPAL_READ_SAMPLE_INCOMPLETE")
    if (
        read.get("provider_isolation") != "PASS"
        or read.get("read_shadow_gate") != "PASS"
        or not _all_zero(read.get("hard_invariants"))
    ):
        codes.append("PRINCIPAL_READ_ZERO_INJECTION_FAILED")
    if _integer(read.get("cleanup_residue")) or not read.get(
        "rollback_verified"
    ):
        codes.append("PRINCIPAL_READ_CLEANUP_OR_ROLLBACK_FAILED")

    lifecycle = _mapping(bundle.get("lifecycle"))
    if (
        lifecycle.get("lifecycle_gate") != "PASS"
        or lifecycle.get("consent_race_safety") != "PASS"
    ):
        codes.append("CONSENT_LIFECYCLE_DRILL_FAILED")
    if any(
        _integer(lifecycle.get(key))
        for key in ("fact_residue", "consent_residue", "cleanup_residue")
    ):
        codes.append("CONSENT_LIFECYCLE_RESIDUE")

    restore = _mapping(bundle.get("restore"))
    if (
        restore.get("backup_restore_tombstone_replay") != "PASS"
        or _integer(restore.get("restore_cycles")) < 3
        or _integer(restore.get("fault_boundaries_exercised")) < 6
        or _integer(restore.get("fault_reclaims_completed")) < 6
    ):
        codes.append("BACKUP_RESTORE_TOMBSTONE_REPLAY_FAILED")
    if _integer(restore.get("restored_private_data_residue")):
        codes.append("RESTORE_PRIVATE_DATA_RESIDUE")
    if not restore.get("public_knowledge_unchanged"):
        codes.append("RESTORE_PUBLIC_KNOWLEDGE_CHANGED")

    status = _mapping(bundle.get("status"))
    automatic_stop = _mapping(status.get("automatic_stop"))
    if automatic_stop.get("triggered") or automatic_stop.get("gate_codes"):
        codes.append("AGGREGATE_HARD_STOP_ACTIVE")
    if status.get("hold_codes"):
        codes.append("AGGREGATE_HOLD_ACTIVE")
    for stage in ("budget", "write", "read"):
        if not _mapping(status.get(stage)).get("sample_sufficient"):
            codes.append(f"{stage.upper()}_AGGREGATE_SAMPLE_INSUFFICIENT")
    if not _mapping(status.get("budget")).get("data_complete"):
        codes.append("AGGREGATE_METRICS_INCOMPLETE")
    if _integer(
        _mapping(status.get("read")).get("prompt_isolation_violation_count")
    ):
        codes.append("AGGREGATE_PROMPT_ISOLATION_VIOLATION")
    if status.get("configuration_changed") is not False:
        codes.append("STATUS_CHANGED_CONFIGURATION")

    security = _mapping(bundle.get("security"))
    if security.get("review_status") != "PASS":
        codes.append("SECURITY_REVIEW_FAILED")
    if any(
        _integer(security.get(key))
        for key in (
            "artifact_violations",
            "hard_stop_count",
            "knowledge_firewall_violations",
            "protected_taxonomy_hits",
        )
    ):
        codes.append("SECURITY_PRIVACY_FAIRNESS_FIREWALL_FAILED")
    if not security.get("public_knowledge_unchanged"):
        codes.append("SECURITY_PUBLIC_KNOWLEDGE_CHANGED")

    repository = _mapping(bundle.get("repository"))
    if not repository.get("safe_defaults"):
        codes.append("SAFE_DEFAULTS_NOT_DISABLED")
    if not repository.get("consume_rejected"):
        codes.append("CONSUME_NOT_REJECTED")
    if not repository.get("rc_revision_is_ancestor"):
        codes.append("RC_REVISION_NOT_ANCESTOR")

    for stage in (
        rc,
        budget,
        write,
        quality,
        read,
        lifecycle,
        restore,
        status,
        security,
    ):
        if stage.get("production_observation") != "NOT_RUN":
            codes.append("PRODUCTION_OBSERVATION_CONTRACT_INVALID")
        if stage.get("long_term_memory_consumption") not in (None, "BLOCKED"):
            codes.append("LONG_TERM_CONSUMPTION_NOT_BLOCKED")

    if codes:
        raise AcceptanceBlocked(codes)
    return SUCCESS_LINES


def build_acceptance_evidence(
    bundle: Mapping[str, object]
) -> dict[str, object]:
    evaluate_operational_shadow(bundle)
    rc = _mapping(bundle["rc"])
    regression = _mapping(bundle["regression"])
    budget = _mapping(bundle["budget"])
    write = _mapping(bundle["write"])
    quality = _mapping(bundle["quality"])
    read = _mapping(bundle["read"])
    restore = _mapping(bundle["restore"])
    security = _mapping(bundle["security"])
    evidence: dict[str, object] = {
        "schema_version": "memory-operational-shadow-evidence-v1",
        "accepted": True,
        "validated_rc_revision": str(rc.get("validated_rc_revision", "")),
        "validation_revision": str(regression.get("validated_revision", "")),
        "environment_category": "isolated_staging",
        "observation_profile": str(budget.get("profile", "")),
        "observation_window": "deterministic_profile_b_matrix",
        "validation_counts": {
            "full_python_passed": _integer(
                _mapping(regression.get("full_python")).get("passed_count")
            ),
            "full_python_skipped": _integer(
                _mapping(regression.get("full_python")).get("skipped")
            ),
            "postgres_executed": _integer(
                _mapping(regression.get("pg_runtime")).get("executed")
            ),
            "postgres_deselected": _integer(
                _mapping(regression.get("pg_runtime")).get("deselected")
            ),
            "frontend_modules": _integer(
                _mapping(regression.get("frontend_build")).get(
                    "modules_transformed"
                )
            ),
            "browser_passed": _integer(
                _mapping(regression.get("full_browser")).get("passed_count")
            ),
            "browser_skipped": _integer(
                _mapping(regression.get("full_browser")).get("skipped")
            ),
        },
        "shadow_samples": {
            "budget_followups": _integer(budget.get("followup_sample_count")),
            "principal_write": _integer(write.get("sample_count")),
            "proposal_reviews": _integer(quality.get("reviewed_count")),
            "principal_read": _integer(read.get("sample_count")),
            "restore_cycles": _integer(restore.get("restore_cycles")),
            "restore_fault_boundaries": _integer(
                restore.get("fault_boundaries_exercised")
            ),
            "artifacts_audited": _integer(
                security.get("artifacts_audited")
            ),
        },
        "aggregate_gates": {
            "staging_isolated": True,
            "migration_validated": True,
            "budget_shadow": "PASS",
            "principal_write_shadow": "PASS",
            "proposal_quality": "PASS",
            "principal_read_zero_injection": "PASS",
            "consent_deletion_restore": "PASS",
            "durable_metrics_complete": True,
            "privacy_security_fairness_firewall": "PASS",
        },
        "cleanup": {
            "test_listeners": _integer(
                _mapping(regression.get("cleanup")).get("test_listeners")
            ),
            "isolated_relation_residue": _integer(
                _mapping(regression.get("cleanup")).get(
                    "isolated_test_relation_residue"
                )
            ),
            "private_data_residue": _integer(
                restore.get("restored_private_data_residue")
            ),
        },
        "safe_defaults": {
            "budget": "disabled",
            "compression": "disabled",
            "principal_memory": "disabled",
            "trusted_local_api": "disabled",
            "consume_rejected": True,
        },
        "production_shadow_approval": "REQUIRED",
        "long_term_memory_consumption": "BLOCKED",
        "production_observation": "NOT_RUN",
    }
    validate_acceptance_artifact(evidence)
    return evidence


def format_blocked_output(codes) -> tuple[str, ...]:
    return (
        "MEMORY_OPERATIONAL_SHADOW=BLOCKED",
        *(f"GATE={code}" for code in sorted(set(codes))),
        "LONG_TERM_MEMORY_CONSUMPTION=BLOCKED",
        "PRODUCTION_OBSERVATION=NOT_RUN",
    )


def _load_json(path: Path) -> Mapping[str, object]:
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError(f"acceptance input {path.name} must be an object")
    return value


def _git_output(*args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode:
        return ""
    return completed.stdout.strip()


def repository_snapshot(rc_revision: str) -> dict[str, bool]:
    config = load_effective_memory_config({})
    safe_defaults = (
        config.budget.mode == "disabled"
        and config.compression.mode == "disabled"
        and config.long_term.mode == "disabled"
        and not config.long_term.write_shadow_enabled
        and not config.long_term.read_shadow_enabled
        and not config.long_term.trusted_local_api_enabled
    )
    consume_rejected = False
    try:
        load_effective_memory_config({"MEMORY_LONG_TERM_MODE": "consume"})
    except ValueError:
        consume_rejected = True
    ancestor = subprocess.run(
        ["git", "merge-base", "--is-ancestor", rc_revision, "HEAD"],
        cwd=ROOT,
        capture_output=True,
    ).returncode == 0
    return {
        "safe_defaults": safe_defaults,
        "consume_rejected": consume_rejected,
        "rc_revision_is_ancestor": ancestor,
    }


def load_default_bundle(*, regression_path: Path) -> dict[str, object]:
    rc = _load_json(ROOT / "docs/memory-validation-operational-evidence.json")
    return {
        "rc": rc,
        "regression": _load_json(regression_path),
        "staging_text": (
            ROOT / "docs/memory-shadow-staging-acceptance.md"
        ).read_text(encoding="utf-8"),
        "budget_text": (
            ROOT / "docs/memory-budget-shadow-acceptance.md"
        ).read_text(encoding="utf-8"),
        "budget": _load_json(ROOT / "docs/memory-budget-shadow-observation.json"),
        "write": _load_json(
            ROOT / "docs/principal-memory-write-shadow-observation.json"
        ),
        "quality": _load_json(
            ROOT / "docs/principal-memory-proposal-quality.json"
        ),
        "read": _load_json(
            ROOT / "docs/principal-memory-read-shadow-observation.json"
        ),
        "lifecycle": _load_json(
            ROOT / "docs/principal-memory-lifecycle-drill-evidence.json"
        ),
        "restore": _load_json(
            ROOT / "docs/memory-shadow-restore-drill-evidence.json"
        ),
        "status": _load_json(ROOT / "docs/memory-shadow-status.json"),
        "security": _load_json(
            ROOT / "docs/memory-shadow-security-review-evidence.json"
        ),
        "repository": repository_snapshot(
            str(rc.get("validated_rc_revision", ""))
        ),
    }


def _audit_private_keys(value: object) -> bool:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if str(key).casefold() in _PRIVATE_KEYS or _audit_private_keys(item):
                return True
    elif isinstance(value, list):
        return any(_audit_private_keys(item) for item in value)
    return False


def validate_acceptance_artifact(value: Mapping[str, object]) -> None:
    if _audit_private_keys(value):
        raise RuntimeError("acceptance evidence contains private data")
    rendered = json.dumps(value, sort_keys=True, ensure_ascii=False).casefold()
    if "postgresql://" in rendered or "redis://" in rendered:
        raise RuntimeError("acceptance evidence contains private connection data")
    if value.get("production_observation") != "NOT_RUN":
        raise RuntimeError("acceptance evidence production state is invalid")
    if value.get("long_term_memory_consumption") != "BLOCKED":
        raise RuntimeError("acceptance evidence consumption state is invalid")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Gate operational Memory Shadow approval material."
    )
    parser.add_argument("--regression-evidence", type=Path, default=DEFAULT_REGRESSION)
    parser.add_argument("--evidence-output", type=Path, default=DEFAULT_OUTPUT)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    bundle = load_default_bundle(regression_path=args.regression_evidence)
    try:
        lines = evaluate_operational_shadow(bundle)
        evidence = build_acceptance_evidence(bundle)
    except AcceptanceBlocked as exc:
        print("\n".join(format_blocked_output(exc.codes)))
        return 1
    args.evidence_output.write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
