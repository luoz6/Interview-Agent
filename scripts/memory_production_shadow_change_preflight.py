from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Mapping

from app.runtime.config.memory import load_effective_memory_config
from contracts.evidence import (
    AtomicEvidenceWriter,
    EvidenceIssuer,
    EvidenceRegistry,
    EvidenceVerifier,
    InputArtifact,
    ProductionBudgetReadinessEvidencePayload,
    ProductionShadowApprovalRequestPayload,
    ProductionShadowChangePreflightEvidencePayload,
    input_artifact_from_bundle,
)
from contracts.evidence.digest import canonical_sha256, sha256_bytes
from contracts.evidence.rendering import render_gate_lines
from contracts.evidence.status import PromotionDecision, VerificationStatus
from contracts.policies import (
    ProductionBudgetReadinessEvidencePolicy,
    ProductionShadowApprovalRequestPolicy,
    ProductionShadowChangePreflightEvidencePolicy,
)
from scripts.postgres_acceptance_support import (
    AcceptanceConfigurationError,
    load_receipt_signer,
    require_environment_value,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_APPROVAL_REQUEST = (
    ROOT / "reports" / "memory" / "production-shadow-approval-request-v1.json"
)
DEFAULT_READINESS_EVIDENCE = (
    ROOT / "reports" / "memory" / "production-budget-readiness-evidence-v1.json"
)
DEFAULT_OUTPUT = (
    ROOT
    / "reports"
    / "memory"
    / "production-shadow-change-preflight-evidence-v1.json"
)
REQUIRED_ROLES = (
    "change_owner",
    "operations",
    "privacy",
    "security",
    "fairness",
)
PASS_LINES = (
    "PRODUCTION_BUDGET_SHADOW_CHANGE_PREFLIGHT=PASS",
    "EXTERNAL_APPROVAL_RECORD=VERIFIED",
    "REQUESTED_PHASE=BUDGET_SHADOW_ONLY",
    "CONFIGURATION_CHANGED=false",
    "PRINCIPAL_WRITE_SHADOW_PRODUCTION=NOT_AUTHORIZED",
    "PRINCIPAL_READ_SHADOW_PRODUCTION=NOT_AUTHORIZED",
    "LONG_TERM_MEMORY_CONSUMPTION=BLOCKED",
    "PRODUCTION_OBSERVATION=NOT_RUN",
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_REVISION = re.compile(r"^[0-9a-f]{7,64}$")


class ChangePreflightBlocked(RuntimeError):
    def __init__(self, codes) -> None:
        self.codes = tuple(sorted(set(codes)))
        super().__init__("production Budget Shadow change preflight blocked")


def canonical_record_sha256(value: Mapping[str, object]) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"))
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


def evaluate_change_preflight(
    *,
    record: Mapping[str, object],
    expected_record_sha256: str,
    actual_record_sha256: str,
    current_revision: str,
    expected_deployment_scope_sha256: str,
    record_is_external: bool,
    now: datetime,
    repository: Mapping[str, object],
) -> tuple[str, ...]:
    codes: list[str] = []
    if not record_is_external:
        codes.append("APPROVAL_RECORD_NOT_EXTERNAL")
    if (
        _SHA256.fullmatch(expected_record_sha256) is None
        or expected_record_sha256 != actual_record_sha256
    ):
        codes.append("APPROVAL_RECORD_HASH_MISMATCH")
    if record.get("approval_status") != "APPROVED":
        codes.append("APPROVAL_STATUS_NOT_APPROVED")

    if repository.get("approval_packet_ready") is not True:
        codes.append("APPROVAL_PACKET_NOT_READY")
    if repository.get("readiness_verified") is not True:
        codes.append("PRODUCTION_READINESS_UNVERIFIED")
    if repository.get("safe_defaults") is not True:
        codes.append("SAFE_DEFAULTS_CHANGED")
    if repository.get("consume_rejected") is not True:
        codes.append("CONSUME_NOT_REJECTED")
    if repository.get("production_observation_not_run") is not True:
        codes.append("PRODUCTION_OBSERVATION_ALREADY_STARTED")
    if repository.get("hard_stop_clear") is not True:
        codes.append("SHADOW_HARD_STOP_ACTIVE")
    if repository.get("configuration_changed") is not False:
        codes.append("PREFLIGHT_CONFIGURATION_ALREADY_CHANGED")

    if record.get("approval_status") == "APPROVED":
        expected_fields = {
            "schema_version",
            "approval_status",
            "requested_phase",
            "approved_revision",
            "deployment_scope_sha256",
            "traffic_percent",
            "window_start",
            "window_end",
            "expires_at",
            "change_ticket_sha256",
            "approvals",
        }
        if set(record) != expected_fields:
            codes.append("APPROVAL_RECORD_FIELDS_INVALID")
        if record.get("schema_version") != (
            "memory-production-shadow-approval-record-v1"
        ):
            codes.append("APPROVAL_RECORD_SCHEMA_INVALID")
        if record.get("requested_phase") != "BUDGET_SHADOW_ONLY":
            codes.append("REQUESTED_PHASE_NOT_BUDGET_ONLY")
        revision = record.get("approved_revision")
        if (
            not isinstance(revision, str)
            or _REVISION.fullmatch(revision) is None
            or revision != current_revision
        ):
            codes.append("APPROVED_REVISION_MISMATCH")
        deployment_digest = record.get("deployment_scope_sha256")
        if (
            not isinstance(deployment_digest, str)
            or _SHA256.fullmatch(deployment_digest) is None
            or deployment_digest != expected_deployment_scope_sha256
        ):
            codes.append("DEPLOYMENT_SCOPE_MISMATCH")
        ticket_digest = record.get("change_ticket_sha256")
        if (
            not isinstance(ticket_digest, str)
            or _SHA256.fullmatch(ticket_digest) is None
        ):
            codes.append("CHANGE_TICKET_BINDING_INVALID")
        traffic_value = record.get("traffic_percent")
        if type(traffic_value) is not float or not 0 < traffic_value <= 1.0:
            codes.append("TRAFFIC_PERCENT_EXCEEDS_APPROVAL")

        start = _timestamp(record.get("window_start"))
        end = _timestamp(record.get("window_end"))
        expires = _timestamp(record.get("expires_at"))
        if start is None or end is None or expires is None:
            codes.append("APPROVED_WINDOW_INVALID")
        else:
            duration_hours = (end - start).total_seconds() / 3600
            if duration_hours < 24:
                codes.append("APPROVED_WINDOW_TOO_SHORT")
            if duration_hours > 168:
                codes.append("APPROVED_WINDOW_TOO_LONG")
            if now < start or now >= end:
                codes.append("OUTSIDE_APPROVED_WINDOW")
            if expires < now:
                codes.append("APPROVAL_RECORD_EXPIRED")
            if expires > end:
                codes.append("APPROVAL_EXPIRY_EXCEEDS_WINDOW")

        approvals = _mapping(record.get("approvals"))
        approval_failure = set(approvals) != set(REQUIRED_ROLES)
        expected_approval_fields = {
            "decision",
            "approver_ref_sha256",
            "decided_at",
        }
        for role in REQUIRED_ROLES:
            approval = _mapping(approvals.get(role))
            decided_at = _timestamp(approval.get("decided_at"))
            approver_digest = approval.get("approver_ref_sha256")
            if (
                set(approval) != expected_approval_fields
                or approval.get("decision") != "APPROVED"
                or not isinstance(approver_digest, str)
                or _SHA256.fullmatch(approver_digest) is None
                or decided_at is None
                or (start is not None and decided_at > start)
            ):
                approval_failure = True
        if approval_failure:
            codes.append("REQUIRED_APPROVAL_NOT_GRANTED")

    if codes:
        raise ChangePreflightBlocked(codes)
    return PASS_LINES


def build_preflight_evidence(
    *,
    record: Mapping[str, object],
    expected_record_sha256: str,
    actual_record_sha256: str,
    current_revision: str,
    expected_deployment_scope_sha256: str,
    record_is_external: bool,
    now: datetime,
    repository: Mapping[str, object],
    approval_request: ProductionShadowApprovalRequestPayload,
    readiness: ProductionBudgetReadinessEvidencePayload,
) -> ProductionShadowChangePreflightEvidencePayload:
    evaluate_change_preflight(
        record=record,
        expected_record_sha256=expected_record_sha256,
        actual_record_sha256=actual_record_sha256,
        current_revision=current_revision,
        expected_deployment_scope_sha256=expected_deployment_scope_sha256,
        record_is_external=record_is_external,
        now=now,
        repository=repository,
    )
    start = _timestamp(record.get("window_start"))
    end = _timestamp(record.get("window_end"))
    duration = int((end - start).total_seconds() / 3600) if start and end else 0
    traffic = record.get("traffic_percent")
    if type(traffic) is not float:
        raise ChangePreflightBlocked(("TRAFFIC_PERCENT_EXCEEDS_APPROVAL",))
    return ProductionShadowChangePreflightEvidencePayload(
        schema_version="production-shadow-change-preflight-evidence-v1",
        validated_revision=current_revision,
        validated_rc_revision=approval_request.validated_rc_revision,
        validation_revision=approval_request.validation_revision,
        approval_request_verified=True,
        readiness_verified=True,
        approval_record_verified=True,
        approval_roles_verified=len(REQUIRED_ROLES),
        record_is_external=True,
        record_hash_match=True,
        revision_match=True,
        deployment_scope_match=True,
        requested_phase="BUDGET_SHADOW_ONLY",
        traffic_percent=traffic,
        window_duration_hours=duration,
        configuration_changed=False,
        principal_write_shadow_production="NOT_AUTHORIZED",
        principal_read_shadow_production="NOT_AUTHORIZED",
        long_term_memory_consumption="BLOCKED",
        production_observation="NOT_RUN",
        synthetic=approval_request.synthetic or readiness.synthetic,
    )


def format_blocked_output(codes) -> tuple[str, ...]:
    return (
        "PRODUCTION_BUDGET_SHADOW_CHANGE_PREFLIGHT=BLOCKED",
        *(f"GATE={code}" for code in sorted(set(codes))),
        "CONFIGURATION_CHANGED=false",
        "LONG_TERM_MEMORY_CONSUMPTION=BLOCKED",
        "PRODUCTION_OBSERVATION=NOT_RUN",
    )


def repository_snapshot(
    *,
    approval_request: ProductionShadowApprovalRequestPayload,
    readiness: ProductionBudgetReadinessEvidencePayload,
) -> dict[str, object]:
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
    approval_result = ProductionShadowApprovalRequestPolicy().evaluate(
        approval_request
    )
    readiness_result = ProductionBudgetReadinessEvidencePolicy().evaluate(readiness)
    revisions_match = (
        readiness.validated_rc_revision == approval_request.validated_rc_revision
        and readiness.validation_revision == approval_request.validation_revision
    )
    return {
        "approval_packet_ready": (
            approval_result.verification_status is VerificationStatus.PASS
            and approval_result.promotion_decision is PromotionDecision.HOLD
            and approval_request.approval_status == "PENDING"
            and revisions_match
        ),
        "readiness_verified": (
            readiness_result.verification_status is VerificationStatus.PASS
            and readiness_result.promotion_decision is PromotionDecision.HOLD
            and revisions_match
        ),
        "safe_defaults": safe_defaults and readiness.safe_defaults,
        "consume_rejected": consume_rejected and readiness.consume_rejected,
        "production_observation_not_run": (
            approval_request.production_observation_not_run
            and readiness.production_observation == "NOT_RUN"
        ),
        "hard_stop_clear": readiness.hard_stop_clear,
        "configuration_changed": (
            approval_request.configuration_changed or readiness.configuration_changed
        ),
    }


def _is_external(path: Path) -> bool:
    resolved = path.resolve()
    try:
        resolved.relative_to(ROOT.resolve())
    except ValueError:
        return True
    return False


def _git_revision() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def _external_record_input(
    *,
    path: Path,
    expected_record_sha256: str,
) -> InputArtifact:
    persisted = path.read_bytes()
    return InputArtifact(
        path="external-production-shadow-approval-record",
        sha256=sha256_bytes(persisted),
        receipt_sha256=expected_record_sha256,
        size_bytes=len(persisted),
        media_type="application/json",
    )


def _verified_upstream_state(
    *,
    approval_request: ProductionShadowApprovalRequestPayload,
    approval_status: VerificationStatus,
    approval_decision: PromotionDecision | None,
    approval_gate_codes: list[str],
    readiness: ProductionBudgetReadinessEvidencePayload,
    readiness_status: VerificationStatus,
    readiness_decision: PromotionDecision | None,
    readiness_gate_codes: list[str],
    readiness_input_receipt_sha256: str | None,
    approval_receipt_sha256: str,
) -> bool:
    return (
        approval_status is VerificationStatus.PASS
        and approval_decision is PromotionDecision.HOLD
        and not approval_gate_codes
        and readiness_status is VerificationStatus.PASS
        and readiness_decision is PromotionDecision.HOLD
        and not readiness_gate_codes
        and readiness_input_receipt_sha256 == approval_receipt_sha256
        and readiness.validated_rc_revision == approval_request.validated_rc_revision
        and readiness.validation_revision == approval_request.validation_revision
        and ProductionShadowApprovalRequestPolicy()
        .evaluate(approval_request)
        .verification_status
        is VerificationStatus.PASS
        and ProductionBudgetReadinessEvidencePolicy()
        .evaluate(readiness)
        .verification_status
        is VerificationStatus.PASS
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate an external production Budget Shadow approval record."
    )
    parser.add_argument(
        "--approval-request",
        type=Path,
        default=DEFAULT_APPROVAL_REQUEST,
    )
    parser.add_argument("--approval-request-revision")
    parser.add_argument(
        "--approval-request-scope",
        default="memory.production-shadow.approval-request",
    )
    parser.add_argument(
        "--readiness-evidence",
        type=Path,
        default=DEFAULT_READINESS_EVIDENCE,
    )
    parser.add_argument("--readiness-revision")
    parser.add_argument(
        "--readiness-scope",
        default="memory.production-budget-shadow.readiness",
    )
    parser.add_argument("--approval-record", type=Path, required=True)
    parser.add_argument("--expected-record-sha256", required=True)
    parser.add_argument("--expected-deployment-scope-sha256", required=True)
    parser.add_argument("--current-revision")
    parser.add_argument("--now")
    parser.add_argument("--output-revision")
    parser.add_argument(
        "--output-scope",
        default="memory.production-shadow.change-preflight",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        approval_revision = (
            args.approval_request_revision
            or require_environment_value(os.environ, "APPROVAL_REQUEST_REVISION")
        )
        readiness_revision = (
            args.readiness_revision
            or require_environment_value(os.environ, "READINESS_EVIDENCE_REVISION")
        )
        output_revision = (
            args.output_revision
            or require_environment_value(os.environ, "EVIDENCE_REVISION")
        )
        signer = load_receipt_signer(os.environ)
        verifier = EvidenceVerifier(
            registry=EvidenceRegistry.default(),
            receipt_signer=signer,
        )
        verified_approval = verifier.verify(
            json.loads(args.approval_request.read_text(encoding="utf-8")),
            expected_revision=approval_revision,
            expected_scope=args.approval_request_scope,
        )
        verified_readiness = verifier.verify(
            json.loads(args.readiness_evidence.read_text(encoding="utf-8")),
            expected_revision=readiness_revision,
            expected_scope=args.readiness_scope,
        )
        if not isinstance(
            verified_approval.payload,
            ProductionShadowApprovalRequestPayload,
        ) or not isinstance(
            verified_readiness.payload,
            ProductionBudgetReadinessEvidencePayload,
        ):
            raise ValueError("production preflight upstream payload type is invalid")
        readiness_manifest = verified_readiness.bundle.artifact.envelope.input_manifest
        readiness_input_receipt = (
            readiness_manifest[0].receipt_sha256
            if len(readiness_manifest) == 1
            else None
        )
        if not _verified_upstream_state(
            approval_request=verified_approval.payload,
            approval_status=verified_approval.bundle.artifact.verification_status,
            approval_decision=verified_approval.bundle.artifact.promotion_decision,
            approval_gate_codes=verified_approval.bundle.artifact.gate_codes,
            readiness=verified_readiness.payload,
            readiness_status=verified_readiness.bundle.artifact.verification_status,
            readiness_decision=verified_readiness.bundle.artifact.promotion_decision,
            readiness_gate_codes=verified_readiness.bundle.artifact.gate_codes,
            readiness_input_receipt_sha256=readiness_input_receipt,
            approval_receipt_sha256=canonical_sha256(
                verified_approval.bundle.receipt
            ),
        ):
            raise ValueError("production preflight upstream evidence state is invalid")
        record_value = json.loads(args.approval_record.read_text(encoding="utf-8"))
        if not isinstance(record_value, Mapping):
            raise ValueError("approval record must be an object")
    except (AcceptanceConfigurationError, OSError, ValueError, json.JSONDecodeError):
        print(
            "\n".join(
                format_blocked_output(("PRODUCTION_PREFLIGHT_INPUT_UNVERIFIED",))
            )
        )
        return 1

    actual_sha = canonical_record_sha256(record_value)
    now = _timestamp(args.now) if args.now else datetime.now(timezone.utc)
    if now is None:
        print("\n".join(format_blocked_output(("PREFLIGHT_TIME_INVALID",))))
        return 1
    kwargs = {
        "record": record_value,
        "expected_record_sha256": args.expected_record_sha256,
        "actual_record_sha256": actual_sha,
        "current_revision": args.current_revision or _git_revision(),
        "expected_deployment_scope_sha256": (
            args.expected_deployment_scope_sha256
        ),
        "record_is_external": _is_external(args.approval_record),
        "now": now,
        "repository": repository_snapshot(
            approval_request=verified_approval.payload,
            readiness=verified_readiness.payload,
        ),
    }
    try:
        lines = evaluate_change_preflight(**kwargs)
        payload = build_preflight_evidence(
            **kwargs,
            approval_request=verified_approval.payload,
            readiness=verified_readiness.payload,
        )
    except ChangePreflightBlocked as exc:
        print("\n".join(format_blocked_output(exc.codes)))
        return 1

    policy_result = ProductionShadowChangePreflightEvidencePolicy().evaluate(payload)
    bundle = EvidenceIssuer(signer=signer).issue(
        payload_type="production-shadow-change-preflight-evidence",
        payload=payload,
        policy_result=policy_result,
        producer="scripts.memory-production-shadow-change-preflight",
        tool_version="2.0.0",
        revision=output_revision,
        scope=args.output_scope,
        input_manifest=(
            input_artifact_from_bundle(
                path=args.approval_request,
                logical_path="production-shadow-approval-request",
                bundle=verified_approval.bundle,
            ),
            input_artifact_from_bundle(
                path=args.readiness_evidence,
                logical_path="production-budget-readiness-evidence",
                bundle=verified_readiness.bundle,
            ),
            _external_record_input(
                path=args.approval_record,
                expected_record_sha256=args.expected_record_sha256,
            ),
        ),
    )
    output_verifier = EvidenceVerifier(
        registry=EvidenceRegistry.default(),
        receipt_signer=signer,
    )
    AtomicEvidenceWriter(
        post_write_verifier=lambda value: output_verifier.verify(
            value,
            expected_revision=output_revision,
            expected_scope=args.output_scope,
        )
    ).write(args.output, bundle)
    print("\n".join((*render_gate_lines(bundle), *lines)))
    return 0 if policy_result.verification_status is VerificationStatus.PASS else 1


if __name__ == "__main__":
    raise SystemExit(main())
