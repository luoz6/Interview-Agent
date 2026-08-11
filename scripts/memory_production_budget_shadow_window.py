from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from math import isfinite
import os
from pathlib import Path
from typing import Mapping

from contracts.evidence import (
    AtomicEvidenceWriter,
    EvidenceIssuer,
    EvidenceRegistry,
    EvidenceVerifier,
    InputArtifact,
    ProductionBudgetWindowDecisionEvidencePayload,
    ProductionShadowChangePreflightEvidencePayload,
    input_artifact_from_bundle,
)
from contracts.evidence.digest import canonical_sha256, sha256_bytes
from contracts.evidence.rendering import render_gate_lines
from contracts.evidence.status import PromotionDecision, VerificationStatus
from contracts.policies import (
    ProductionBudgetWindowDecisionEvidencePolicy,
    ProductionShadowChangePreflightEvidencePolicy,
)
from scripts.postgres_acceptance_support import (
    AcceptanceConfigurationError,
    load_receipt_signer,
    require_environment_value,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PREFLIGHT_EVIDENCE = (
    ROOT
    / "reports"
    / "memory"
    / "production-shadow-change-preflight-evidence-v1.json"
)
DEFAULT_OUTPUT = (
    ROOT
    / "reports"
    / "memory"
    / "production-budget-window-decision-evidence-v1.json"
)
SCHEMA_VERSION = "memory-production-budget-shadow-window-input-v1"
OUTPUT_SCHEMA_VERSION = "memory-production-budget-shadow-window-decision-v1"
STATES = frozenset(
    {
        "PENDING_APPROVAL",
        "PREFLIGHT_VERIFIED",
        "WARM_UP",
        "OBSERVING",
        "STOPPING",
        "CLOSED",
    }
)
ACTIONS = frozenset(
    {
        "HOLD",
        "START_WARM_UP",
        "KEEP_WARM_UP",
        "RAMP_TO_APPROVED_CAP",
        "STOP_NOW",
        "CLOSE_SCHEDULED",
    }
)
INPUT_FIELDS = frozenset(
    {
        "schema_version",
        "state",
        "approval_record_verified",
        "approval_current",
        "inside_approved_window",
        "revision_match",
        "deployment_scope_verified",
        "configuration_match",
        "configuration_single_axis",
        "other_memory_axis_enabled",
        "data_complete",
        "max_consecutive_missing_minute_buckets",
        "hard_stop_count",
        "approved_traffic_percent",
        "observed_traffic_percent",
        "warmup_duration_minutes",
        "warmup_followup_sample_count",
        "scheduled_end_reached",
        "manual_stop_requested",
    }
)
BOOLEAN_FIELDS = frozenset(
    {
        "approval_record_verified",
        "approval_current",
        "inside_approved_window",
        "revision_match",
        "deployment_scope_verified",
        "configuration_match",
        "configuration_single_axis",
        "other_memory_axis_enabled",
        "data_complete",
        "scheduled_end_reached",
        "manual_stop_requested",
    }
)


class WindowInputBlocked(RuntimeError):
    def __init__(self, codes) -> None:
        self.codes = tuple(sorted(set(codes)))
        super().__init__("production Budget Shadow window input blocked")


@dataclass(frozen=True)
class WindowDecision:
    action: str
    next_state: str
    gate_codes: tuple[str, ...]


def _valid_number(value: object, *, integer: bool = False) -> bool:
    if isinstance(value, bool):
        return False
    if integer:
        return isinstance(value, int) and value >= 0
    return (
        isinstance(value, (int, float))
        and isfinite(float(value))
        and float(value) >= 0
    )


def _is_external(path: Path) -> bool:
    try:
        path.resolve().relative_to(ROOT.resolve())
    except ValueError:
        return True
    return False


def validate_window_input(value: Mapping[str, object]) -> None:
    codes: list[str] = []
    if set(value) != set(INPUT_FIELDS):
        codes.append("WINDOW_INPUT_FIELD_SET_INVALID")
    if value.get("schema_version") != SCHEMA_VERSION:
        codes.append("WINDOW_INPUT_SCHEMA_INVALID")
    if value.get("state") not in STATES:
        codes.append("WINDOW_STATE_INVALID")
    for field in BOOLEAN_FIELDS:
        if not isinstance(value.get(field), bool):
            codes.append(f"WINDOW_BOOLEAN_INVALID_{field.upper()}")
    for field in (
        "max_consecutive_missing_minute_buckets",
        "hard_stop_count",
        "warmup_followup_sample_count",
    ):
        if not _valid_number(value.get(field), integer=True):
            codes.append(f"WINDOW_INTEGER_INVALID_{field.upper()}")
    for field in (
        "approved_traffic_percent",
        "observed_traffic_percent",
        "warmup_duration_minutes",
    ):
        if not _valid_number(value.get(field)):
            codes.append(f"WINDOW_NUMBER_INVALID_{field.upper()}")
    approved = value.get("approved_traffic_percent")
    if _valid_number(approved) and not 0 < float(approved) <= 1.0:
        codes.append("APPROVED_TRAFFIC_PERCENT_INVALID")
    if codes:
        raise WindowInputBlocked(codes)


def _unsafe_runtime_gates(value: Mapping[str, object]) -> list[str]:
    gates: list[str] = []
    if not bool(value.get("approval_record_verified")):
        gates.append("APPROVAL_RECORD_NOT_VERIFIED")
    if not bool(value.get("approval_current")) or not bool(
        value.get("inside_approved_window")
    ):
        gates.append("APPROVAL_NOT_CURRENT")
    if not bool(value.get("revision_match")):
        gates.append("APPROVED_REVISION_MISMATCH")
    if not bool(value.get("deployment_scope_verified")):
        gates.append("DEPLOYMENT_SCOPE_MISMATCH")
    if not bool(value.get("configuration_match")) or not bool(
        value.get("configuration_single_axis")
    ):
        gates.append("CONFIGURATION_DRIFT")
    if bool(value.get("other_memory_axis_enabled")):
        gates.append("OTHER_MEMORY_AXIS_ENABLED")
    if not bool(value.get("data_complete")) or int(
        value.get("max_consecutive_missing_minute_buckets", 0)
    ) >= 2:
        gates.append("DURABLE_METRICS_INCOMPLETE")
    if int(value.get("hard_stop_count", 0)) > 0:
        gates.append("HARD_STOP_ACTIVE")
    if float(value.get("observed_traffic_percent", 0.0)) > min(
        float(value.get("approved_traffic_percent", 0.0)), 1.0
    ):
        gates.append("TRAFFIC_CAP_EXCEEDED")
    return sorted(set(gates))


def decide_window_action(value: Mapping[str, object]) -> WindowDecision:
    validate_window_input(value)
    state = str(value["state"])
    if state == "CLOSED":
        return WindowDecision("HOLD", "CLOSED", ())
    if state == "STOPPING":
        return WindowDecision("HOLD", "STOPPING", ())

    unsafe = _unsafe_runtime_gates(value)
    if state == "PENDING_APPROVAL":
        return WindowDecision("HOLD", state, tuple(unsafe))
    if bool(value.get("manual_stop_requested")):
        return WindowDecision("STOP_NOW", "STOPPING", ("MANUAL_STOP",))
    if bool(value.get("scheduled_end_reached")):
        return WindowDecision("CLOSE_SCHEDULED", "STOPPING", ())
    if unsafe:
        return WindowDecision("STOP_NOW", "STOPPING", tuple(unsafe))
    if state == "PREFLIGHT_VERIFIED":
        return WindowDecision("START_WARM_UP", "WARM_UP", ())
    if state == "WARM_UP":
        if (
            float(value.get("warmup_duration_minutes", 0.0)) >= 30
            and int(value.get("warmup_followup_sample_count", 0)) >= 20
        ):
            return WindowDecision(
                "RAMP_TO_APPROVED_CAP", "OBSERVING", ()
            )
        return WindowDecision("KEEP_WARM_UP", "WARM_UP", ())
    return WindowDecision("HOLD", "OBSERVING", ())


def build_decision_artifact(
    value: Mapping[str, object], decision: WindowDecision
) -> dict[str, object]:
    if decision.action not in ACTIONS or decision.next_state not in STATES:
        raise RuntimeError("invalid production window decision")
    return {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "current_state": value.get("state"),
        "action": decision.action,
        "next_state": decision.next_state,
        "gate_codes": list(decision.gate_codes),
        "configuration_changed": False,
        "principal_write_shadow_production": "NOT_AUTHORIZED",
        "principal_read_shadow_production": "NOT_AUTHORIZED",
        "long_term_memory_consumption": "BLOCKED",
    }


def build_decision_payload(
    value: Mapping[str, object],
    decision: WindowDecision,
    *,
    preflight: ProductionShadowChangePreflightEvidencePayload,
) -> ProductionBudgetWindowDecisionEvidencePayload:
    artifact = build_decision_artifact(value, decision)
    return ProductionBudgetWindowDecisionEvidencePayload(
        schema_version="production-budget-window-decision-evidence-v1",
        source_preflight_verified=True,
        current_state=str(artifact["current_state"]),
        action=str(artifact["action"]),
        next_state=str(artifact["next_state"]),
        decision_gate_codes=list(decision.gate_codes),
        configuration_changed=False,
        principal_write_shadow_production="NOT_AUTHORIZED",
        principal_read_shadow_production="NOT_AUTHORIZED",
        long_term_memory_consumption="BLOCKED",
        synthetic=preflight.synthetic,
    )


def format_blocked_output(codes) -> tuple[str, ...]:
    return (
        "PRODUCTION_BUDGET_SHADOW_WINDOW=BLOCKED",
        *(f"GATE={code}" for code in sorted(set(codes))),
        "CONFIGURATION_CHANGED=false",
        "LONG_TERM_MEMORY_CONSUMPTION=BLOCKED",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Choose a deterministic Production Budget Shadow action."
    )
    parser.add_argument(
        "--preflight-evidence",
        type=Path,
        default=DEFAULT_PREFLIGHT_EVIDENCE,
    )
    parser.add_argument("--preflight-revision")
    parser.add_argument(
        "--preflight-scope",
        default="memory.production-shadow.change-preflight",
    )
    parser.add_argument("--state-input", type=Path, required=True)
    parser.add_argument("--expected-state-sha256", required=True)
    parser.add_argument("--output-revision")
    parser.add_argument(
        "--output-scope",
        default="memory.production-budget-shadow.window-decision",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not _is_external(args.state_input):
        print("\n".join(format_blocked_output(("WINDOW_INPUT_NOT_EXTERNAL",))))
        return 1
    try:
        preflight_revision = (
            args.preflight_revision
            or require_environment_value(os.environ, "PREFLIGHT_EVIDENCE_REVISION")
        )
        output_revision = (
            args.output_revision
            or require_environment_value(os.environ, "EVIDENCE_REVISION")
        )
        signer = load_receipt_signer(os.environ)
        verified_preflight = EvidenceVerifier(
            registry=EvidenceRegistry.default(),
            receipt_signer=signer,
        ).verify(
            json.loads(args.preflight_evidence.read_text(encoding="utf-8")),
            expected_revision=preflight_revision,
            expected_scope=args.preflight_scope,
        )
        if not isinstance(
            verified_preflight.payload,
            ProductionShadowChangePreflightEvidencePayload,
        ):
            raise ValueError("production preflight payload type is invalid")
        preflight = verified_preflight.payload
        expected_decision = (
            PromotionDecision.HOLD if preflight.synthetic else PromotionDecision.READY
        )
        preflight_result = ProductionShadowChangePreflightEvidencePolicy().evaluate(
            preflight
        )
        manifest_paths = {
            item.path
            for item in verified_preflight.bundle.artifact.envelope.input_manifest
        }
        if (
            verified_preflight.bundle.artifact.verification_status
            is not VerificationStatus.PASS
            or verified_preflight.bundle.artifact.promotion_decision
            is not expected_decision
            or verified_preflight.bundle.artifact.gate_codes
            or preflight_result.verification_status is not VerificationStatus.PASS
            or preflight_result.promotion_decision is not expected_decision
            or manifest_paths
            != {
                "production-shadow-approval-request",
                "production-budget-readiness-evidence",
                "external-production-shadow-approval-record",
            }
        ):
            raise ValueError("production preflight evidence state is invalid")
        value = json.loads(args.state_input.read_text(encoding="utf-8"))
        if not isinstance(value, Mapping):
            raise ValueError("window input must be an object")
        actual_state_sha256 = canonical_sha256(value)
        if (
            not isinstance(args.expected_state_sha256, str)
            or len(args.expected_state_sha256) != 64
            or actual_state_sha256 != args.expected_state_sha256
        ):
            raise ValueError("window input digest mismatch")
        if (
            value.get("approval_record_verified") is not True
            or value.get("revision_match") is not True
            or value.get("deployment_scope_verified") is not True
            or type(value.get("approved_traffic_percent")) is not float
            or value["approved_traffic_percent"] > preflight.traffic_percent
        ):
            raise ValueError("window input is not bound to preflight")
    except (AcceptanceConfigurationError, OSError, ValueError, json.JSONDecodeError):
        print("\n".join(format_blocked_output(("WINDOW_INPUT_UNVERIFIED",))))
        return 1
    try:
        decision = decide_window_action(value)
    except WindowInputBlocked as exc:
        print("\n".join(format_blocked_output(exc.codes)))
        return 1
    payload = build_decision_payload(value, decision, preflight=preflight)
    policy_result = ProductionBudgetWindowDecisionEvidencePolicy().evaluate(payload)
    state_bytes = args.state_input.read_bytes()
    state_artifact = InputArtifact(
        path="external-production-budget-window-state",
        sha256=sha256_bytes(state_bytes),
        receipt_sha256=args.expected_state_sha256,
        size_bytes=len(state_bytes),
        media_type="application/json",
    )
    bundle = EvidenceIssuer(signer=signer).issue(
        payload_type="production-budget-window-decision-evidence",
        payload=payload,
        policy_result=policy_result,
        producer="scripts.memory-production-budget-shadow-window",
        tool_version="2.0.0",
        revision=output_revision,
        scope=args.output_scope,
        input_manifest=(
            input_artifact_from_bundle(
                path=args.preflight_evidence,
                logical_path="production-shadow-change-preflight-evidence",
                bundle=verified_preflight.bundle,
            ),
            state_artifact,
        ),
    )
    output_verifier = EvidenceVerifier(
        registry=EvidenceRegistry.default(),
        receipt_signer=signer,
    )
    AtomicEvidenceWriter(
        post_write_verifier=lambda persisted: output_verifier.verify(
            persisted,
            expected_revision=output_revision,
            expected_scope=args.output_scope,
        )
    ).write(args.output, bundle)
    print("\n".join(render_gate_lines(bundle)))
    print(f"PRODUCTION_BUDGET_SHADOW_WINDOW={decision.action}")
    for code in decision.gate_codes:
        print(f"GATE={code}")
    print("CONFIGURATION_CHANGED=false")
    print("PRINCIPAL_WRITE_SHADOW_PRODUCTION=NOT_AUTHORIZED")
    print("PRINCIPAL_READ_SHADOW_PRODUCTION=NOT_AUTHORIZED")
    print("LONG_TERM_MEMORY_CONSUMPTION=BLOCKED")
    if policy_result.verification_status is not VerificationStatus.PASS:
        return 1
    return 1 if decision.action == "STOP_NOW" else 0


if __name__ == "__main__":
    raise SystemExit(main())
