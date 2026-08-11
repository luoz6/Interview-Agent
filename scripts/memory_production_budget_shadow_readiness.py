from __future__ import annotations

import argparse
from datetime import datetime, timezone
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
    ProductionBudgetReadinessEvidencePayload,
    ProductionShadowApprovalRequestPayload,
    input_artifact_from_bundle,
)
from contracts.evidence.rendering import render_gate_lines
from contracts.evidence.status import PromotionDecision, VerificationStatus
from contracts.policies import (
    ProductionBudgetReadinessEvidencePolicy,
    ProductionShadowApprovalRequestPolicy,
)
from scripts.memory_production_budget_shadow_acceptance import evaluate_observation
from scripts.memory_production_budget_shadow_observation import sanitize_aggregate_input
from scripts.memory_production_budget_shadow_window import decide_window_action
from scripts.memory_production_shadow_change_preflight import (
    ChangePreflightBlocked,
    canonical_record_sha256,
    evaluate_change_preflight,
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
DEFAULT_OUTPUT = (
    ROOT / "reports" / "memory" / "production-budget-readiness-evidence-v1.json"
)
SUCCESS_LINES = (
    "PRODUCTION_BUDGET_SHADOW_TOOLING=READY_FOR_REVIEW",
    "APPROVAL_STATUS=PENDING",
    "CHANGE_PREFLIGHT=BLOCKED",
    "CONFIGURATION_CHANGED=false",
    "PRODUCTION_OBSERVATION=NOT_RUN",
    "PRINCIPAL_WRITE_SHADOW_PRODUCTION=NOT_AUTHORIZED",
    "PRINCIPAL_READ_SHADOW_PRODUCTION=NOT_AUTHORIZED",
    "LONG_TERM_MEMORY_CONSUMPTION=BLOCKED",
)
CONTRACT_PATHS = (
    "docs/memory-production-budget-shadow-observation-contract.md",
    "docs/memory-production-budget-shadow-acceptance-contract.md",
    "docs/memory-production-budget-shadow-runbook.md",
)
OFFLINE_SCRIPTS = (
    "scripts/memory_production_budget_shadow_observation.py",
    "scripts/memory_production_budget_shadow_acceptance.py",
    "scripts/memory_production_budget_shadow_window.py",
)
_FORBIDDEN_IMPORT = re.compile(
    r"^\s*(?:from|import)\s+(?:app\.|psycopg|requests|httpx)",
    re.MULTILINE,
)
_EXPECTED_PENDING_EXAMPLE_CODES = [
    "APPROVAL_RECORD_NOT_EXTERNAL",
    "APPROVAL_STATUS_NOT_APPROVED",
]


class ReadinessBlocked(RuntimeError):
    def __init__(self, codes) -> None:
        self.codes = tuple(sorted(set(codes)))
        super().__init__("production Budget Shadow tooling readiness blocked")


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


def _pending_example_codes(repository: Mapping[str, object]) -> tuple[str, ...]:
    record = json.loads(
        (
            ROOT / "docs/memory-production-shadow-approval-record.example.json"
        ).read_text(encoding="utf-8")
    )
    try:
        evaluate_change_preflight(
            record=record,
            expected_record_sha256=canonical_record_sha256(record),
            actual_record_sha256=canonical_record_sha256(record),
            current_revision=_git_revision(),
            expected_deployment_scope_sha256="0" * 64,
            record_is_external=False,
            now=datetime.now(timezone.utc),
            repository=repository,
        )
    except ChangePreflightBlocked as exc:
        return exc.codes
    return ()


def _window_probe() -> Mapping[str, object]:
    return {
        "schema_version": "memory-production-budget-shadow-window-input-v1",
        "state": "PREFLIGHT_VERIFIED",
        "approval_record_verified": True,
        "approval_current": True,
        "inside_approved_window": True,
        "revision_match": True,
        "deployment_scope_verified": True,
        "configuration_match": True,
        "configuration_single_axis": True,
        "other_memory_axis_enabled": False,
        "data_complete": True,
        "max_consecutive_missing_minute_buckets": 0,
        "hard_stop_count": 0,
        "approved_traffic_percent": 1.0,
        "observed_traffic_percent": 0.0,
        "warmup_duration_minutes": 0.0,
        "warmup_followup_sample_count": 0,
        "scheduled_end_reached": False,
        "manual_stop_requested": False,
    }


def _approval_request_is_safe(
    approval_request: ProductionShadowApprovalRequestPayload,
) -> bool:
    result = ProductionShadowApprovalRequestPolicy().evaluate(approval_request)
    return (
        result.verification_status is VerificationStatus.PASS
        and result.promotion_decision is PromotionDecision.HOLD
        and approval_request.approval_status == "PENDING"
        and approval_request.requested_phase == "BUDGET_SHADOW_ONLY"
        and not approval_request.configuration_changed
        and approval_request.production_observation_not_run
        and approval_request.long_term_consumption_blocked
        and not approval_request.provider_input_change
        and not approval_request.budget_enforcement
        and not approval_request.compression_consumption
        and not approval_request.principal_write_shadow
        and not approval_request.principal_read_shadow
        and not approval_request.principal_memory_consumption
        and not approval_request.production_migration
    )


def build_repository_snapshot(
    approval_request: ProductionShadowApprovalRequestPayload,
    *,
    approval_request_verified: bool,
) -> dict[str, object]:
    config = load_effective_memory_config({})
    safe_defaults = (
        config.budget.mode == "disabled"
        and not config.budget.shadow_enabled
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

    fixture = json.loads(
        (
            ROOT
            / "tests/fixtures/memory_production_budget_shadow/pass_candidate.json"
        ).read_text(encoding="utf-8")
    )
    observation = sanitize_aggregate_input(fixture).artifact
    acceptance = evaluate_observation(observation)
    window = decide_window_action(_window_probe())
    source_audit = all(
        _FORBIDDEN_IMPORT.search((ROOT / path).read_text(encoding="utf-8"))
        is None
        for path in OFFLINE_SCRIPTS
    )
    approval_request_safe = _approval_request_is_safe(approval_request)
    hard_stop_clear = (
        approval_request_verified
        and approval_request_safe
        and approval_request.production_observation_not_run
        and approval_request.long_term_consumption_blocked
    )
    change_repository = {
        "approval_packet_ready": approval_request_verified and approval_request_safe,
        "readiness_verified": True,
        "safe_defaults": safe_defaults,
        "consume_rejected": consume_rejected,
        "production_observation_not_run": (
            approval_request.production_observation_not_run
        ),
        "hard_stop_clear": hard_stop_clear,
        "configuration_changed": False,
    }
    return {
        "validated_revision": _git_revision(),
        "contracts_present": all((ROOT / path).is_file() for path in CONTRACT_PATHS),
        "offline_source_audit": source_audit,
        "observation_probe_status": acceptance.status,
        "window_probe_action": window.action,
        "safe_defaults": safe_defaults,
        "consume_rejected": consume_rejected,
        "approval_request_verified": approval_request_verified,
        "approval_request_safe": approval_request_safe,
        "hard_stop_clear": hard_stop_clear,
        "production_observation_not_run": (
            approval_request.production_observation_not_run
        ),
        "configuration_changed": False,
        "external_approval_input_used": False,
        "pending_example_gate_codes": list(
            _pending_example_codes(change_repository)
        ),
    }


def evaluate_readiness(value: Mapping[str, object]) -> tuple[str, ...]:
    codes: list[str] = []
    if value.get("contracts_present") is not True:
        codes.append("PRODUCTION_CONTRACTS_MISSING")
    if value.get("offline_source_audit") is not True:
        codes.append("PRODUCTION_TOOLING_NOT_OFFLINE")
    if value.get("observation_probe_status") != "PASS":
        codes.append("PRODUCTION_OBSERVATION_PROBE_NOT_GREEN")
    if value.get("window_probe_action") != "START_WARM_UP":
        codes.append("PRODUCTION_WINDOW_PROBE_NOT_GREEN")
    if value.get("safe_defaults") is not True:
        codes.append("SAFE_DEFAULTS_CHANGED")
    if value.get("consume_rejected") is not True:
        codes.append("CONSUME_NOT_REJECTED")
    if value.get("approval_request_verified") is not True:
        codes.append("PRODUCTION_APPROVAL_REQUEST_UNVERIFIED")
    if value.get("approval_request_safe") is not True:
        codes.append("PRODUCTION_APPROVAL_REQUEST_UNSAFE")
    if value.get("hard_stop_clear") is not True:
        codes.append("SHADOW_HARD_STOP_ACTIVE")
    if value.get("production_observation_not_run") is not True:
        codes.append("PRODUCTION_OBSERVATION_ALREADY_STARTED")
    if value.get("configuration_changed") is not False:
        codes.append("READINESS_CONFIGURATION_CHANGED")
    if value.get("external_approval_input_used") is not False:
        codes.append("EXTERNAL_APPROVAL_INPUT_NOT_ALLOWED")
    if value.get("pending_example_gate_codes") != _EXPECTED_PENDING_EXAMPLE_CODES:
        codes.append("PENDING_EXAMPLE_FAIL_CLOSED_INVALID")
    revision = value.get("validated_revision")
    if not isinstance(revision, str) or re.fullmatch(r"[0-9a-f]{7,64}", revision) is None:
        codes.append("VALIDATED_REVISION_INVALID")
    if codes:
        raise ReadinessBlocked(codes)
    return SUCCESS_LINES


def build_readiness_evidence(
    value: Mapping[str, object],
    *,
    approval_request: ProductionShadowApprovalRequestPayload,
) -> ProductionBudgetReadinessEvidencePayload:
    evaluate_readiness(value)
    return ProductionBudgetReadinessEvidencePayload(
        schema_version="production-budget-readiness-evidence-v1",
        validated_revision=str(value["validated_revision"]),
        validated_rc_revision=approval_request.validated_rc_revision,
        validation_revision=approval_request.validation_revision,
        approval_request_verified=True,
        contracts_present=True,
        offline_source_audit=True,
        observation_probe_status="PASS",
        window_probe_action="START_WARM_UP",
        safe_defaults=True,
        consume_rejected=True,
        hard_stop_clear=True,
        pending_example_gate_codes=list(_EXPECTED_PENDING_EXAMPLE_CODES),
        approval_status="PENDING",
        requested_phase="BUDGET_SHADOW_ONLY",
        change_preflight="BLOCKED",
        configuration_changed=False,
        production_observation="NOT_RUN",
        principal_write_shadow_production="NOT_AUTHORIZED",
        principal_read_shadow_production="NOT_AUTHORIZED",
        long_term_memory_consumption="BLOCKED",
        synthetic=approval_request.synthetic,
    )


def format_blocked_output(codes) -> tuple[str, ...]:
    return (
        "PRODUCTION_BUDGET_SHADOW_TOOLING=BLOCKED",
        *(f"GATE={code}" for code in sorted(set(codes))),
        "APPROVAL_STATUS=PENDING",
        "CHANGE_PREFLIGHT=BLOCKED",
        "CONFIGURATION_CHANGED=false",
        "PRODUCTION_OBSERVATION=NOT_RUN",
        "LONG_TERM_MEMORY_CONSUMPTION=BLOCKED",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate Production Budget Shadow tooling readiness."
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
    parser.add_argument("--output-revision")
    parser.add_argument(
        "--output-scope",
        default="memory.production-budget-shadow.readiness",
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
        output_revision = (
            args.output_revision
            or require_environment_value(os.environ, "EVIDENCE_REVISION")
        )
        signer = load_receipt_signer(os.environ)
        approval_value = json.loads(
            args.approval_request.read_text(encoding="utf-8")
        )
        verified_approval = EvidenceVerifier(
            registry=EvidenceRegistry.default(),
            receipt_signer=signer,
        ).verify(
            approval_value,
            expected_revision=approval_revision,
            expected_scope=args.approval_request_scope,
        )
        if not isinstance(
            verified_approval.payload,
            ProductionShadowApprovalRequestPayload,
        ):
            raise ValueError("approval request payload type is invalid")
        approval_artifact = verified_approval.bundle.artifact
        if (
            approval_artifact.verification_status is not VerificationStatus.PASS
            or approval_artifact.promotion_decision is not PromotionDecision.HOLD
            or approval_artifact.gate_codes
            or not _approval_request_is_safe(verified_approval.payload)
        ):
            raise ValueError("approval request evidence state is invalid")
    except (AcceptanceConfigurationError, OSError, ValueError, json.JSONDecodeError):
        print(
            "\n".join(
                format_blocked_output(("PRODUCTION_APPROVAL_REQUEST_UNVERIFIED",))
            )
        )
        return 1

    snapshot = build_repository_snapshot(
        verified_approval.payload,
        approval_request_verified=True,
    )
    try:
        lines = evaluate_readiness(snapshot)
        payload = build_readiness_evidence(
            snapshot,
            approval_request=verified_approval.payload,
        )
    except ReadinessBlocked as exc:
        print("\n".join(format_blocked_output(exc.codes)))
        return 1

    policy_result = ProductionBudgetReadinessEvidencePolicy().evaluate(payload)
    bundle = EvidenceIssuer(signer=signer).issue(
        payload_type="production-budget-readiness-evidence",
        payload=payload,
        policy_result=policy_result,
        producer="scripts.memory-production-budget-shadow-readiness",
        tool_version="2.0.0",
        revision=output_revision,
        scope=args.output_scope,
        input_manifest=(
            input_artifact_from_bundle(
                path=args.approval_request,
                logical_path="production-shadow-approval-request",
                bundle=verified_approval.bundle,
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
