from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import subprocess
from typing import Mapping, Sequence

from scripts.hosted_v2_productization_preflight import (
    ProductizationPreflightBlocked,
    canonical_document_sha256,
    canonical_record_sha256,
    evaluate_productization_preflight,
    record_path_is_external,
    repository_snapshot,
)


ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "docs/principal-memory-production-data-use-spec-v1.md"
SCHEMA_VERSION = "principal-memory-production-data-use-decision-v1"
PURPOSES = frozenset(
    {"proposal_write", "fact_storage", "read_shadow", "assist_c1a"}
)
REQUIRED_APPROVAL_ROLES = (
    "product",
    "privacy",
    "security",
    "legal",
    "fairness",
    "operations",
)
REQUIRED_REVIEW_ROLES = ("accessibility", "interview_quality")
REQUIRED_DECISION_FIELDS = frozenset(
    {
        "schema_version",
        "decision",
        "spec_sha256",
        "repository_revision",
        "deployment_scope_sha256",
        "approved_regions",
        "purposes",
        "taxonomy_version",
        "candidate_notice_version",
        "retention_schedule_version",
        "controller_and_processor_roles",
        "lawful_basis_and_consent_requirements",
        "provider_and_subprocessors",
        "provider_retention_and_training_setting",
        "cross_border_transfer_mechanism",
        "human_review_protocol_version",
        "deletion_export_slo",
        "disable_slo",
        "decision_time",
        "expires_at",
        "revalidation_trigger",
        "approvals",
        "reviews",
    }
)
REQUIRED_APPROVAL_FIELDS = frozenset({"decision", "external_ref", "decided_at"})
REQUIRED_REVIEW_FIELDS = frozenset({"decision", "external_ref", "decided_at"})
PASS_LINES = (
    "PRINCIPAL_MEMORY_DATA_USE_PREFLIGHT=PASS",
    "HOSTED_PRODUCTIZATION_DECISION=VERIFIED_GO",
    "PRODUCTION_DATA_USE_SPEC=VERIFIED_APPROVED",
    "BUDGET_AND_CONTROL_FOUNDATION_DECISION_GATES=UNBLOCKED",
    "CONFIGURATION_CHANGED=false",
    "PRINCIPAL_WRITE_SHADOW_PRODUCTION=NOT_AUTHORIZED",
    "PRINCIPAL_READ_SHADOW_PRODUCTION=NOT_AUTHORIZED",
    "PRODUCTION_CANARY=NOT_AUTHORIZED",
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_REVISION = re.compile(r"^[0-9a-f]{40}$")
_PLACEHOLDER = re.compile(
    r"^(?:tbd|todo|unknown|unset|none|n/?a|placeholder|\*)$",
    re.IGNORECASE,
)


class DataUsePreflightBlocked(RuntimeError):
    def __init__(self, codes: Sequence[str]) -> None:
        self.codes = tuple(sorted(set(codes)))
        super().__init__("Principal Memory Data-use preflight blocked")


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
    return bool(candidate) and _PLACEHOLDER.fullmatch(candidate) is None


def _concrete_string_list(value: object) -> bool:
    return (
        isinstance(value, list)
        and bool(value)
        and all(_concrete_string(item) for item in value)
    )


def evaluate_data_use_preflight(
    *,
    productization_record: Mapping[str, object],
    expected_productization_record_sha256: str,
    actual_productization_record_sha256: str,
    actual_adr_sha256: str,
    productization_record_is_external: bool,
    data_use_record: Mapping[str, object],
    expected_data_use_record_sha256: str,
    actual_data_use_record_sha256: str,
    actual_spec_sha256: str,
    expected_deployment_scope_sha256: str,
    data_use_record_is_external: bool,
    current_revision: str,
    now: datetime,
    repository: Mapping[str, object],
) -> tuple[str, ...]:
    codes: list[str] = []
    try:
        evaluate_productization_preflight(
            record=productization_record,
            expected_record_sha256=expected_productization_record_sha256,
            actual_record_sha256=actual_productization_record_sha256,
            actual_adr_sha256=actual_adr_sha256,
            current_revision=current_revision,
            record_is_external=productization_record_is_external,
            now=now,
            repository=repository,
        )
    except ProductizationPreflightBlocked:
        codes.append("PRODUCTIZATION_GATE_NOT_VERIFIED")

    if not data_use_record_is_external:
        codes.append("DATA_USE_RECORD_NOT_EXTERNAL")
    if _SHA256.fullmatch(expected_data_use_record_sha256) is None:
        codes.append("EXPECTED_DATA_USE_RECORD_HASH_INVALID")
    elif expected_data_use_record_sha256 != actual_data_use_record_sha256:
        codes.append("DATA_USE_RECORD_HASH_MISMATCH")
    if set(data_use_record) != REQUIRED_DECISION_FIELDS:
        codes.append("DATA_USE_RECORD_FIELDS_INVALID")
    if data_use_record.get("schema_version") != SCHEMA_VERSION:
        codes.append("DATA_USE_RECORD_SCHEMA_INVALID")
    if data_use_record.get("decision") != "APPROVED":
        codes.append("DATA_USE_DECISION_NOT_APPROVED")

    spec_digest = str(data_use_record.get("spec_sha256") or "").lower()
    if (
        _SHA256.fullmatch(spec_digest) is None
        or spec_digest != actual_spec_sha256.lower()
    ):
        codes.append("DATA_USE_SPEC_DIGEST_MISMATCH")
    revision = str(data_use_record.get("repository_revision") or "").lower()
    if (
        _REVISION.fullmatch(revision) is None
        or revision != current_revision.lower()
    ):
        codes.append("DATA_USE_REPOSITORY_REVISION_MISMATCH")
    deployment_digest = str(
        data_use_record.get("deployment_scope_sha256") or ""
    ).lower()
    if (
        _SHA256.fullmatch(deployment_digest) is None
        or deployment_digest != expected_deployment_scope_sha256.lower()
    ):
        codes.append("DATA_USE_DEPLOYMENT_SCOPE_MISMATCH")

    product_regions = productization_record.get("approved_regions")
    data_regions = data_use_record.get("approved_regions")
    if (
        not _concrete_string_list(data_regions)
        or not isinstance(product_regions, list)
        or set(data_regions) != set(product_regions)
    ):
        codes.append("DATA_USE_REGION_MISMATCH")
    purposes = data_use_record.get("purposes")
    if not isinstance(purposes, list) or set(purposes) != PURPOSES:
        codes.append("CONSENT_PURPOSES_INCOMPLETE")

    for field, code in (
        ("taxonomy_version", "TAXONOMY_VERSION_MISSING"),
        ("candidate_notice_version", "CANDIDATE_NOTICE_VERSION_MISSING"),
        ("retention_schedule_version", "RETENTION_VERSION_MISSING"),
        ("controller_and_processor_roles", "PROCESSOR_ROLES_MISSING"),
        ("lawful_basis_and_consent_requirements", "LAWFUL_BASIS_MISSING"),
        ("provider_retention_and_training_setting", "PROVIDER_POLICY_MISSING"),
        ("cross_border_transfer_mechanism", "TRANSFER_MECHANISM_MISSING"),
        ("human_review_protocol_version", "REVIEW_PROTOCOL_MISSING"),
        ("revalidation_trigger", "DATA_USE_REVALIDATION_TRIGGER_MISSING"),
    ):
        if not _concrete_string(data_use_record.get(field)):
            codes.append(code)
    if not _concrete_string_list(data_use_record.get("provider_and_subprocessors")):
        codes.append("PROVIDER_AND_SUBPROCESSORS_MISSING")
    if data_use_record.get("deletion_export_slo") != "24_HOURS":
        codes.append("DELETION_EXPORT_SLO_MISMATCH")
    if data_use_record.get("disable_slo") != "NEXT_ASSEMBLY_MAX_60_SECONDS":
        codes.append("DISABLE_SLO_MISMATCH")

    product_decision_time = _timestamp(productization_record.get("decision_time"))
    data_decision_time = _timestamp(data_use_record.get("decision_time"))
    expires_at = _timestamp(data_use_record.get("expires_at"))
    if data_decision_time is None or expires_at is None:
        codes.append("DATA_USE_VALIDITY_INVALID")
    else:
        if now < data_decision_time or now >= expires_at:
            codes.append("DATA_USE_DECISION_NOT_CURRENT")
        if expires_at <= data_decision_time or expires_at - data_decision_time > timedelta(days=180):
            codes.append("DATA_USE_VALIDITY_TOO_LONG")
        if product_decision_time is None or data_decision_time < product_decision_time:
            codes.append("DATA_USE_PRECEDES_PRODUCTIZATION_GO")

    approvals = _mapping(data_use_record.get("approvals"))
    approval_failure = set(approvals) != set(REQUIRED_APPROVAL_ROLES)
    for role in REQUIRED_APPROVAL_ROLES:
        approval = _mapping(approvals.get(role))
        approval_time = _timestamp(approval.get("decided_at"))
        if (
            set(approval) != REQUIRED_APPROVAL_FIELDS
            or approval.get("decision") != "APPROVED"
            or not _concrete_string(approval.get("external_ref"))
            or approval_time is None
            or (data_decision_time is not None and approval_time > data_decision_time)
        ):
            approval_failure = True
    if approval_failure:
        codes.append("REQUIRED_DATA_USE_APPROVAL_NOT_GRANTED")

    reviews = _mapping(data_use_record.get("reviews"))
    review_failure = set(reviews) != set(REQUIRED_REVIEW_ROLES)
    for role in REQUIRED_REVIEW_ROLES:
        review = _mapping(reviews.get(role))
        review_time = _timestamp(review.get("decided_at"))
        if (
            set(review) != REQUIRED_REVIEW_FIELDS
            or review.get("decision") != "REVIEWED"
            or not _concrete_string(review.get("external_ref"))
            or review_time is None
            or (data_decision_time is not None and review_time > data_decision_time)
        ):
            review_failure = True
    if review_failure:
        codes.append("REQUIRED_CANDIDATE_COPY_REVIEW_NOT_COMPLETE")

    if codes:
        raise DataUsePreflightBlocked(codes)
    return PASS_LINES


def format_blocked_output(codes: Sequence[str]) -> tuple[str, ...]:
    return (
        "PRINCIPAL_MEMORY_DATA_USE_PREFLIGHT=BLOCKED",
        *(f"GATE={code}" for code in sorted(set(codes))),
        "CONFIGURATION_CHANGED=false",
        "PRODUCTION_DATA_USE_SPEC=NOT_APPROVED",
        "REAL_CANDIDATE_PROCESSING=PROHIBITED",
        "PRINCIPAL_WRITE_SHADOW_PRODUCTION=NOT_AUTHORIZED",
        "PRINCIPAL_READ_SHADOW_PRODUCTION=NOT_AUTHORIZED",
        "PRODUCTION_CANARY=NOT_AUTHORIZED",
    )


def _read_record(path: Path) -> Mapping[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError("decision record must be a JSON object")
    return value


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
            "Verify external Productization and Principal Memory Data-use "
            "decisions without changing configuration."
        )
    )
    parser.add_argument("--productization-record", type=Path, required=True)
    parser.add_argument("--expected-productization-sha256", required=True)
    parser.add_argument("--data-use-record", type=Path, required=True)
    parser.add_argument("--expected-data-use-sha256", required=True)
    parser.add_argument("--expected-deployment-scope-sha256", required=True)
    parser.add_argument("--now", help="ISO-8601 verification time; defaults to UTC now")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    product_path = args.productization_record.resolve()
    data_path = args.data_use_record.resolve()
    now = _timestamp(args.now) if args.now else datetime.now(timezone.utc)
    if now is None:
        raise SystemExit("--now must be a timezone-aware ISO-8601 timestamp")

    try:
        product_record = _read_record(product_path)
        data_record = _read_record(data_path)
        lines = evaluate_data_use_preflight(
            productization_record=product_record,
            expected_productization_record_sha256=(
                args.expected_productization_sha256.lower()
            ),
            actual_productization_record_sha256=canonical_record_sha256(
                product_record
            ),
            actual_adr_sha256=canonical_document_sha256(
                ROOT / "docs/hosted-v2-productization-adr.md"
            ),
            productization_record_is_external=record_path_is_external(product_path),
            data_use_record=data_record,
            expected_data_use_record_sha256=args.expected_data_use_sha256.lower(),
            actual_data_use_record_sha256=canonical_record_sha256(data_record),
            actual_spec_sha256=canonical_document_sha256(SPEC),
            expected_deployment_scope_sha256=(
                args.expected_deployment_scope_sha256.lower()
            ),
            data_use_record_is_external=record_path_is_external(data_path),
            current_revision=_current_revision(),
            now=now,
            repository=repository_snapshot(),
        )
    except (OSError, json.JSONDecodeError, ValueError):
        lines = format_blocked_output(("DECISION_RECORD_UNREADABLE",))
        exit_code = 2
    except DataUsePreflightBlocked as exc:
        lines = format_blocked_output(exc.codes)
        exit_code = 2
    else:
        exit_code = 0

    for line in lines:
        print(line)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
