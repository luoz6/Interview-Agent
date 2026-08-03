from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
import subprocess
from typing import Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
ADR = ROOT / "docs/hosted-v2-productization-adr.md"
BASELINE = ROOT / "docs/long-term-memory-production-execution-baseline.md"
PLAN = ROOT / (
    "docs/superpowers/plans/"
    "2026-08-03-long-term-memory-production-shadows-consumption-and-promotion.md"
)
ENV_EXAMPLE = ROOT / ".env.example"
SCHEMA_VERSION = "hosted-v2-productization-decision-v1"
REQUIRED_ROLES = (
    "product",
    "change_owner",
    "operations",
    "privacy",
    "security",
    "fairness",
    "accessibility",
    "interview_quality",
    "legal",
)
REQUIRED_RECORD_FIELDS = frozenset(
    {
        "schema_version",
        "decision",
        "adr_sha256",
        "repository_revision",
        "product_scope",
        "deployment_model",
        "approved_regions",
        "oidc_provider_class",
        "account_recovery_model",
        "support_and_on_call_model",
        "local_v1_unchanged",
        "data_use_spec_still_required",
        "decision_time",
        "expires_at",
        "review_expiry_or_revalidation_trigger",
        "approvals",
    }
)
REQUIRED_APPROVAL_FIELDS = frozenset({"decision", "external_ref", "decided_at"})
PASS_LINES = (
    "HOSTED_PRODUCTIZATION_DECISION_PREFLIGHT=PASS",
    "EXTERNAL_PRODUCTIZATION_DECISION=VERIFIED_GO",
    "TASK_2_DATA_USE_REVIEW=UNBLOCKED",
    "TASKS_4_TO_34=BLOCKED_PENDING_DATA_USE_APPROVAL",
    "CONFIGURATION_CHANGED=false",
    "REAL_CANDIDATE_PROCESSING=PROHIBITED",
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_REVISION = re.compile(r"^[0-9a-f]{40}$")
_FORBIDDEN_PLACEHOLDER = re.compile(
    r"^(?:tbd|todo|unknown|unset|none|n/?a|placeholder|\*)$",
    re.IGNORECASE,
)


class ProductizationPreflightBlocked(RuntimeError):
    def __init__(self, codes: Sequence[str]) -> None:
        self.codes = tuple(sorted(set(codes)))
        super().__init__("Hosted V2 productization preflight blocked")


def canonical_record_sha256(value: Mapping[str, object]) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return sha256(canonical.encode("utf-8")).hexdigest()


def canonical_document_sha256(path: Path) -> str:
    canonical = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    return sha256(canonical.encode("utf-8")).hexdigest()


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def _concrete_string(value: object) -> bool:
    if not isinstance(value, str):
        return False
    candidate = value.strip()
    return bool(candidate) and _FORBIDDEN_PLACEHOLDER.fullmatch(candidate) is None


def record_path_is_external(path: Path) -> bool:
    resolved = path.resolve()
    return resolved != ROOT and ROOT not in resolved.parents


def repository_snapshot() -> dict[str, object]:
    baseline = BASELINE.read_text(encoding="utf-8")
    plan = PLAN.read_text(encoding="utf-8")
    env = ENV_EXAMPLE.read_text(encoding="utf-8")
    safe_defaults = all(
        assignment in env
        for assignment in (
            "MEMORY_BUDGET_MODE=disabled",
            "MEMORY_COMPRESSION_MODE=disabled",
            "MEMORY_TRUSTED_LOCAL_PRINCIPAL_MEMORY_API_ENABLED=false",
            "MEMORY_LONG_TERM_MODE=disabled",
            "MEMORY_LONG_TERM_WRITE_SHADOW_ENABLED=false",
            "MEMORY_LONG_TERM_READ_SHADOW_ENABLED=false",
        )
    )
    production_unauthorized = all(
        state in baseline
        for state in (
            "PRODUCTION_BUDGET_SHADOW=NOT_RUN",
            "PRINCIPAL_WRITE_SHADOW_PRODUCTION=NOT_AUTHORIZED",
            "PRINCIPAL_READ_SHADOW_PRODUCTION=NOT_AUTHORIZED",
            "IMPLEMENTATION=NOT_AUTHORIZED",
            "PRODUCTION_CANARY=NOT_AUTHORIZED",
        )
    )
    return {
        "execution_baseline_frozen": "EXECUTION_BASELINE=FROZEN" in baseline,
        "plan_revision_revised": "**Plan revision:** v0.2-revised" in plan,
        "safe_defaults": safe_defaults,
        "production_unauthorized": production_unauthorized,
        "configuration_changed": False,
    }


def evaluate_productization_preflight(
    *,
    record: Mapping[str, object],
    expected_record_sha256: str,
    actual_record_sha256: str,
    actual_adr_sha256: str,
    current_revision: str,
    record_is_external: bool,
    now: datetime,
    repository: Mapping[str, object],
) -> tuple[str, ...]:
    codes: list[str] = []
    if not record_is_external:
        codes.append("DECISION_RECORD_NOT_EXTERNAL")
    if _SHA256.fullmatch(expected_record_sha256) is None:
        codes.append("EXPECTED_RECORD_HASH_INVALID")
    elif expected_record_sha256 != actual_record_sha256:
        codes.append("DECISION_RECORD_HASH_MISMATCH")

    if repository.get("execution_baseline_frozen") is not True:
        codes.append("EXECUTION_BASELINE_NOT_FROZEN")
    if repository.get("plan_revision_revised") is not True:
        codes.append("PLAN_REVISION_MISMATCH")
    if repository.get("safe_defaults") is not True:
        codes.append("SAFE_DEFAULTS_CHANGED")
    if repository.get("production_unauthorized") is not True:
        codes.append("PRODUCTION_STATE_ALREADY_CHANGED")
    if repository.get("configuration_changed") is not False:
        codes.append("PREFLIGHT_CONFIGURATION_ALREADY_CHANGED")

    if record.get("schema_version") != SCHEMA_VERSION:
        codes.append("DECISION_RECORD_SCHEMA_INVALID")
    if set(record) != REQUIRED_RECORD_FIELDS:
        codes.append("DECISION_RECORD_FIELDS_INVALID")
    if record.get("decision") != "GO":
        codes.append("PRODUCTIZATION_DECISION_NOT_GO")
    if record.get("product_scope") != "HOSTED_MULTI_USER_V2":
        codes.append("PRODUCT_SCOPE_MISMATCH")
    if record.get("deployment_model") != "DEPLOYMENT_SCOPED_MULTI_USER":
        codes.append("DEPLOYMENT_MODEL_MISMATCH")
    if record.get("local_v1_unchanged") is not True:
        codes.append("LOCAL_V1_BOUNDARY_NOT_ACCEPTED")
    if record.get("data_use_spec_still_required") is not True:
        codes.append("DATA_USE_GATE_BYPASSED")
    if record.get("account_recovery_model") != (
        "NO_AUTOMATIC_MEMORY_INHERITANCE"
    ):
        codes.append("ACCOUNT_RECOVERY_MODEL_UNSAFE")

    adr_digest = str(record.get("adr_sha256") or "").lower()
    if (
        _SHA256.fullmatch(adr_digest) is None
        or adr_digest != actual_adr_sha256.lower()
    ):
        codes.append("ADR_DIGEST_MISMATCH")
    revision = str(record.get("repository_revision") or "").lower()
    if (
        _REVISION.fullmatch(revision) is None
        or revision != current_revision.lower()
    ):
        codes.append("REPOSITORY_REVISION_MISMATCH")

    regions = record.get("approved_regions")
    if (
        not isinstance(regions, list)
        or not regions
        or not all(_concrete_string(region) for region in regions)
    ):
        codes.append("APPROVED_REGIONS_MISSING")
    for field, code in (
        ("oidc_provider_class", "OIDC_PROVIDER_CLASS_MISSING"),
        ("support_and_on_call_model", "SUPPORT_MODEL_MISSING"),
        ("review_expiry_or_revalidation_trigger", "REVALIDATION_TRIGGER_MISSING"),
    ):
        if not _concrete_string(record.get(field)):
            codes.append(code)

    decided_at = _timestamp(record.get("decision_time"))
    expires_at = _timestamp(record.get("expires_at"))
    if decided_at is None or expires_at is None:
        codes.append("DECISION_VALIDITY_INVALID")
    else:
        if now < decided_at or now >= expires_at:
            codes.append("DECISION_NOT_CURRENT")
        if expires_at <= decided_at or expires_at - decided_at > timedelta(days=180):
            codes.append("DECISION_VALIDITY_TOO_LONG")

    approvals = _mapping(record.get("approvals"))
    approval_failure = set(approvals) != set(REQUIRED_ROLES)
    for role in REQUIRED_ROLES:
        approval = _mapping(approvals.get(role))
        approval_time = _timestamp(approval.get("decided_at"))
        if (
            set(approval) != REQUIRED_APPROVAL_FIELDS
            or approval.get("decision") != "APPROVED"
            or not _concrete_string(approval.get("external_ref"))
            or approval_time is None
            or (decided_at is not None and approval_time > decided_at)
        ):
            approval_failure = True
    if approval_failure:
        codes.append("REQUIRED_PRODUCTIZATION_APPROVAL_NOT_GRANTED")

    if codes:
        raise ProductizationPreflightBlocked(codes)
    return PASS_LINES


def format_blocked_output(codes: Sequence[str]) -> tuple[str, ...]:
    return (
        "HOSTED_PRODUCTIZATION_DECISION_PREFLIGHT=BLOCKED",
        *(f"GATE={code}" for code in sorted(set(codes))),
        "CONFIGURATION_CHANGED=false",
        "HOSTED_PRODUCTIZATION_DECISION=NOT_APPROVED",
        "TASKS_4_TO_34=BLOCKED_BY_PRODUCTIZATION_AND_DATA_USE_GATES",
        "REAL_CANDIDATE_PROCESSING=PROHIBITED",
    )


def _current_revision() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip().lower()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Verify an external Hosted V2 productization GO record without "
            "changing repository or production configuration."
        )
    )
    parser.add_argument("--record", type=Path, required=True)
    parser.add_argument("--expected-record-sha256", required=True)
    parser.add_argument("--now", help="ISO-8601 verification time; defaults to UTC now")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    record_path = args.record.resolve()
    now = _timestamp(args.now) if args.now else datetime.now(timezone.utc)
    if now is None:
        raise SystemExit("--now must be a timezone-aware ISO-8601 timestamp")

    try:
        raw = json.loads(record_path.read_text(encoding="utf-8"))
        if not isinstance(raw, Mapping):
            raise ValueError("decision record must be a JSON object")
        lines = evaluate_productization_preflight(
            record=raw,
            expected_record_sha256=args.expected_record_sha256.lower(),
            actual_record_sha256=canonical_record_sha256(raw),
            actual_adr_sha256=canonical_document_sha256(ADR),
            current_revision=_current_revision(),
            record_is_external=record_path_is_external(record_path),
            now=now,
            repository=repository_snapshot(),
        )
    except (OSError, json.JSONDecodeError, ValueError):
        lines = format_blocked_output(("DECISION_RECORD_UNREADABLE",))
        exit_code = 2
    except ProductizationPreflightBlocked as exc:
        lines = format_blocked_output(exc.codes)
        exit_code = 2
    else:
        exit_code = 0

    for line in lines:
        print(line)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
