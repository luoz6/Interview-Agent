from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Mapping

from scripts.memory_production_budget_shadow_observation import (
    AggregateInputBlocked,
    validate_observation_artifact,
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate a sanitized Production Budget Shadow observation."
    )
    parser.add_argument("--observation", type=Path, required=True)
    args = parser.parse_args(argv)
    value = json.loads(args.observation.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        value = {}
    decision = evaluate_observation(value)
    print("\n".join(render_decision(decision, value)))
    return 0 if decision.status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
