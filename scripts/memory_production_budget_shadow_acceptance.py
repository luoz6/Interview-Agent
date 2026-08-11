from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Mapping

from contracts.evidence import (
    AtomicEvidenceWriter,
    EvidenceIssuer,
    EvidenceRegistry,
    EvidenceVerifier,
    ProductionBudgetAcceptanceEvidencePayload,
    ProductionBudgetObservationEvidencePayload,
    ProductionBudgetWindowDecisionEvidencePayload,
    input_artifact_from_bundle,
)
from contracts.evidence.rendering import render_gate_lines
from contracts.evidence.status import PromotionDecision, VerificationStatus
from contracts.policies import (
    ProductionBudgetAcceptanceEvidencePolicy,
    ProductionBudgetObservationEvidencePolicy,
    ProductionBudgetWindowDecisionEvidencePolicy,
)
from scripts.memory_production_budget_shadow_observation import (
    AggregateInputBlocked,
    OUTPUT_SCHEMA_VERSION,
    validate_observation_artifact,
)
from scripts.postgres_acceptance_support import (
    AcceptanceConfigurationError,
    load_receipt_signer,
    require_environment_value,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OBSERVATION_EVIDENCE = (
    ROOT / "reports" / "memory" / "production-budget-observation-evidence-v1.json"
)
DEFAULT_WINDOW_EVIDENCE = (
    ROOT
    / "reports"
    / "memory"
    / "production-budget-window-decision-evidence-v1.json"
)
DEFAULT_OUTPUT = (
    ROOT / "reports" / "memory" / "production-budget-acceptance-evidence-v1.json"
)


@dataclass(frozen=True)
class ProductionBudgetShadowDecision:
    status: str
    gate_codes: tuple[str, ...]


def _int(value: Mapping[str, object], key: str) -> int:
    return int(value.get(key, 0))


def _float(value: Mapping[str, object], key: str) -> float:
    return float(value.get(key, 0.0))


def evaluate_observation(
    record: Mapping[str, object],
) -> ProductionBudgetShadowDecision:
    hard: list[str] = []
    insufficient: list[str] = []
    try:
        validate_observation_artifact(record)
    except AggregateInputBlocked as exc:
        return ProductionBudgetShadowDecision("BLOCKED", exc.codes)

    if not bool(record.get("approval_record_verified")):
        hard.append("APPROVAL_RECORD_NOT_VERIFIED")
    if not bool(record.get("approval_current")) or not bool(
        record.get("window_match")
    ):
        hard.append("APPROVAL_NOT_CURRENT")
    if not bool(record.get("revision_match")):
        hard.append("APPROVED_REVISION_MISMATCH")
    if not bool(record.get("deployment_scope_verified")):
        hard.append("DEPLOYMENT_SCOPE_MISMATCH")
    if _float(record, "observed_traffic_percent_max") > min(
        _float(record, "approved_traffic_percent"), 1.0
    ):
        hard.append("TRAFFIC_CAP_EXCEEDED")
    if _int(record, "mandatory_current_content_losses") > 0:
        hard.append("MANDATORY_CURRENT_CONTENT_LOSS")
    if _int(record, "provider_input_change_count") > 0:
        hard.append("PROVIDER_INPUT_CHANGED")
    if _int(record, "known_over_budget_provider_calls") > 0:
        hard.append("KNOWN_OVER_BUDGET_PROVIDER_CALL")
    if _int(record, "privacy_audit_hits") > 0:
        hard.append("PRIVACY_AUDIT_HIT")
    if bool(record.get("budget_config_conflict")) or not bool(
        record.get("configuration_single_axis")
    ):
        hard.append("BUDGET_CONFIG_CONFLICT")
    if bool(record.get("other_memory_axis_enabled")):
        hard.append("OTHER_MEMORY_AXIS_ENABLED")
    if not bool(record.get("data_complete")) or _int(
        record, "max_consecutive_missing_minute_buckets"
    ) >= 2:
        hard.append("DURABLE_METRICS_INCOMPLETE")
    if _int(record, "shadow_execution_error_count") > 0:
        hard.append("SHADOW_EXECUTION_ERROR")
    if _int(record, "deterministic_interview_regression_count") > 0:
        hard.append("DETERMINISTIC_INTERVIEW_REGRESSION")
    if _int(record, "configuration_drift_count") > 0:
        hard.append("CONFIGURATION_DRIFT")
    if not bool(record.get("observation_window_closed")):
        hard.append("OBSERVATION_WINDOW_NOT_CLOSED")
    if not bool(record.get("rollback_verified")):
        hard.append("ROLLBACK_NOT_VERIFIED")
    if not bool(record.get("configuration_restored")):
        hard.append("CONFIGURATION_NOT_RESTORED")
    if _int(record, "new_shadow_events_after_close") > 0:
        hard.append("SHADOW_EVENTS_AFTER_CLOSE")
    if _int(record, "active_listener_residue") > 0:
        hard.append("ACTIVE_LISTENER_RESIDUE")
    if _int(record, "temporary_relation_residue") > 0:
        hard.append("TEMPORARY_RELATION_RESIDUE")

    sample_count = _int(record, "followup_sample_count")
    if sample_count >= 200:
        error_delta = _float(record, "observed_error_rate") - _float(
            record, "baseline_error_rate"
        )
        if error_delta > 0.005:
            hard.append("FOLLOWUP_ERROR_RATE_REGRESSION")
        baseline_p95 = _float(record, "baseline_p95_latency_ms")
        if baseline_p95 > 0 and _float(
            record, "observed_p95_latency_ms"
        ) > baseline_p95 * 1.20:
            hard.append("FOLLOWUP_P95_LATENCY_REGRESSION")

    if hard:
        return ProductionBudgetShadowDecision(
            "BLOCKED", tuple(sorted(set(hard)))
        )

    if _float(record, "observed_traffic_percent_max") <= 0:
        insufficient.append("OBSERVED_TRAFFIC_ZERO")
    if _float(record, "warmup_duration_minutes") < 30:
        insufficient.append("WARMUP_DURATION_INSUFFICIENT")
    if _int(record, "warmup_followup_sample_count") < 20:
        insufficient.append("WARMUP_SAMPLE_INSUFFICIENT")
    if _float(record, "observation_window_duration_hours") < 24:
        insufficient.append("OBSERVATION_WINDOW_TOO_SHORT")
    if sample_count < 200:
        insufficient.append("FOLLOWUP_SAMPLE_INSUFFICIENT")
    if _int(record, "control_sample_count") <= 0:
        insufficient.append("CONTROL_SAMPLE_MISSING")
    if _int(record, "shadow_sample_count") <= 0:
        insufficient.append("SHADOW_SAMPLE_MISSING")
    if _float(record, "baseline_p95_latency_ms") <= 0:
        insufficient.append("BASELINE_LATENCY_MISSING")

    if insufficient:
        return ProductionBudgetShadowDecision(
            "CONTINUE_OBSERVATION",
            tuple(sorted(set(insufficient))),
        )
    return ProductionBudgetShadowDecision("PASS", ())


def render_decision(
    decision: ProductionBudgetShadowDecision,
    record: Mapping[str, object],
) -> tuple[str, ...]:
    restored = (
        "disabled"
        if record.get("configuration_restored") is True
        else "NOT_VERIFIED"
    )
    lines = [f"PRODUCTION_BUDGET_SHADOW={decision.status}"]
    lines.extend(f"GATE={code}" for code in decision.gate_codes)
    lines.extend(
        (
            "OBSERVATION_WINDOW=CLOSED"
            if record.get("observation_window_closed") is True
            else "OBSERVATION_WINDOW=NOT_CLOSED",
            f"CONFIGURATION_RESTORED={restored}",
        )
    )
    if decision.status == "CONTINUE_OBSERVATION":
        lines.append("NEW_APPROVAL_WINDOW_REQUIRED=true")
    lines.extend(
        (
            "PRINCIPAL_WRITE_SHADOW_PRODUCTION=NOT_AUTHORIZED",
            "PRINCIPAL_READ_SHADOW_PRODUCTION=NOT_AUTHORIZED",
            "LONG_TERM_MEMORY_CONSUMPTION=BLOCKED",
        )
    )
    return tuple(lines)


def observation_record(
    payload: ProductionBudgetObservationEvidencePayload,
) -> dict[str, object]:
    value = payload.model_dump(mode="json")
    value.pop("source_preflight_verified")
    value.pop("synthetic")
    value["schema_version"] = OUTPUT_SCHEMA_VERSION
    return value


def build_acceptance_payload(
    decision: ProductionBudgetShadowDecision,
    record: Mapping[str, object],
    *,
    observation: ProductionBudgetObservationEvidencePayload,
    window: ProductionBudgetWindowDecisionEvidencePayload,
) -> ProductionBudgetAcceptanceEvidencePayload:
    return ProductionBudgetAcceptanceEvidencePayload(
        schema_version="production-budget-acceptance-evidence-v1",
        source_observation_verified=True,
        source_window_verified=True,
        observation_revision=observation.approved_revision,
        decision_status=decision.status,
        decision_gate_codes=list(decision.gate_codes),
        observation_window=(
            "CLOSED"
            if record.get("observation_window_closed") is True
            else "NOT_CLOSED"
        ),
        configuration_restored=(
            "disabled"
            if record.get("configuration_restored") is True
            else "NOT_VERIFIED"
        ),
        new_approval_window_required=(
            decision.status == "CONTINUE_OBSERVATION"
        ),
        principal_write_shadow_production="NOT_AUTHORIZED",
        principal_read_shadow_production="NOT_AUTHORIZED",
        long_term_memory_consumption="BLOCKED",
        synthetic=observation.synthetic or window.synthetic,
    )


def format_blocked_output(codes) -> tuple[str, ...]:
    return (
        "PRODUCTION_BUDGET_SHADOW=BLOCKED",
        *(f"GATE={code}" for code in sorted(set(codes))),
        "PRINCIPAL_WRITE_SHADOW_PRODUCTION=NOT_AUTHORIZED",
        "PRINCIPAL_READ_SHADOW_PRODUCTION=NOT_AUTHORIZED",
        "LONG_TERM_MEMORY_CONSUMPTION=BLOCKED",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evaluate a protected Production Budget Shadow observation."
    )
    parser.add_argument(
        "--observation-evidence",
        type=Path,
        default=DEFAULT_OBSERVATION_EVIDENCE,
    )
    parser.add_argument("--observation-revision")
    parser.add_argument(
        "--observation-scope",
        default="memory.production-budget-shadow.observation",
    )
    parser.add_argument(
        "--window-evidence",
        type=Path,
        default=DEFAULT_WINDOW_EVIDENCE,
    )
    parser.add_argument("--window-revision")
    parser.add_argument(
        "--window-scope",
        default="memory.production-budget-shadow.window-decision",
    )
    parser.add_argument("--output-revision")
    parser.add_argument(
        "--output-scope",
        default="memory.production-budget-shadow.acceptance",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        observation_revision = (
            args.observation_revision
            or require_environment_value(os.environ, "OBSERVATION_EVIDENCE_REVISION")
        )
        window_revision = (
            args.window_revision
            or require_environment_value(os.environ, "WINDOW_EVIDENCE_REVISION")
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
        verified_observation = verifier.verify(
            json.loads(args.observation_evidence.read_text(encoding="utf-8")),
            expected_revision=observation_revision,
            expected_scope=args.observation_scope,
        )
        verified_window = verifier.verify(
            json.loads(args.window_evidence.read_text(encoding="utf-8")),
            expected_revision=window_revision,
            expected_scope=args.window_scope,
        )
        if not isinstance(
            verified_observation.payload,
            ProductionBudgetObservationEvidencePayload,
        ) or not isinstance(
            verified_window.payload,
            ProductionBudgetWindowDecisionEvidencePayload,
        ):
            raise ValueError("production acceptance payload type is invalid")
        observation = verified_observation.payload
        window = verified_window.payload
        observation_result = ProductionBudgetObservationEvidencePolicy().evaluate(
            observation
        )
        expected_observation_decision = (
            PromotionDecision.HOLD
            if observation.synthetic
            else PromotionDecision.CONTINUE_OBSERVATION
        )
        window_result = ProductionBudgetWindowDecisionEvidencePolicy().evaluate(window)
        observation_manifest_paths = {
            item.path
            for item in verified_observation.bundle.artifact.envelope.input_manifest
        }
        window_manifest_paths = {
            item.path
            for item in verified_window.bundle.artifact.envelope.input_manifest
        }
        if (
            verified_observation.bundle.artifact.verification_status
            is not VerificationStatus.PASS
            or verified_observation.bundle.artifact.promotion_decision
            is not expected_observation_decision
            or verified_observation.bundle.artifact.gate_codes
            or observation_result.verification_status is not VerificationStatus.PASS
            or observation_result.promotion_decision
            is not expected_observation_decision
            or observation_manifest_paths
            != {
                "production-shadow-change-preflight-evidence",
                "external-production-budget-aggregate",
            }
            or verified_window.bundle.artifact.verification_status
            is not VerificationStatus.PASS
            or verified_window.bundle.artifact.promotion_decision
            is not PromotionDecision.HOLD
            or verified_window.bundle.artifact.gate_codes
            or window_result.verification_status is not VerificationStatus.PASS
            or window_result.promotion_decision is not PromotionDecision.HOLD
            or window_manifest_paths
            != {
                "production-shadow-change-preflight-evidence",
                "external-production-budget-window-state",
            }
            or window.current_state != "CLOSED"
            or window.action != "HOLD"
            or window.next_state != "CLOSED"
        ):
            raise ValueError("production acceptance upstream state is invalid")
    except (AcceptanceConfigurationError, OSError, ValueError, json.JSONDecodeError):
        print("\n".join(format_blocked_output(("ACCEPTANCE_INPUT_UNVERIFIED",))))
        return 1

    record = observation_record(observation)
    decision = evaluate_observation(record)
    payload = build_acceptance_payload(
        decision,
        record,
        observation=observation,
        window=window,
    )
    policy_result = ProductionBudgetAcceptanceEvidencePolicy().evaluate(payload)
    bundle = EvidenceIssuer(signer=signer).issue(
        payload_type="production-budget-acceptance-evidence",
        payload=payload,
        policy_result=policy_result,
        producer="scripts.memory-production-budget-shadow-acceptance",
        tool_version="2.0.0",
        revision=output_revision,
        scope=args.output_scope,
        input_manifest=(
            input_artifact_from_bundle(
                path=args.observation_evidence,
                logical_path="production-budget-observation-evidence",
                bundle=verified_observation.bundle,
            ),
            input_artifact_from_bundle(
                path=args.window_evidence,
                logical_path="production-budget-window-decision-evidence",
                bundle=verified_window.bundle,
            ),
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
    print("\n".join(render_decision(decision, record)))
    if policy_result.verification_status is not VerificationStatus.PASS:
        return 1
    return 0 if decision.status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
