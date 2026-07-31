from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Mapping

from scripts.memory_budget_shadow_observe import LANGUAGES, SCENARIOS


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OBSERVATION = ROOT / "docs" / "memory-budget-shadow-observation.json"

SUCCESS_LINES = (
    "BUDGET_SHADOW_STAGING=PASS",
    "BUDGET_ENFORCEMENT=BLOCKED",
    "PRINCIPAL_MEMORY_SHADOW=NOT_RUN",
    "PRODUCTION_OBSERVATION=NOT_RUN",
)


@dataclass(frozen=True)
class BudgetShadowDecision:
    status: str
    gate_codes: tuple[str, ...]


def _int(record: Mapping[str, object], key: str) -> int:
    try:
        return int(record.get(key, 0))
    except (TypeError, ValueError):
        return 0


def _float(record: Mapping[str, object], key: str) -> float:
    try:
        return float(record.get(key, 0.0))
    except (TypeError, ValueError):
        return 0.0


def evaluate_observation(record: Mapping[str, object]) -> BudgetShadowDecision:
    hard: list[str] = []
    continue_observation: list[str] = []
    if _int(record, "known_over_budget_provider_calls") > 0:
        hard.append("KNOWN_OVER_BUDGET_PROVIDER_CALL")
    if _int(record, "mandatory_current_content_losses") > 0:
        hard.append("MANDATORY_CURRENT_CONTENT_LOSS")
    if _int(record, "provider_input_change_count") > 0:
        hard.append("PROVIDER_INPUT_CHANGED")
    if _int(record, "privacy_audit_hits") > 0:
        hard.append("PRIVACY_AUDIT_HIT")
    if bool(record.get("budget_config_conflict")):
        hard.append("BUDGET_CONFIG_CONFLICT")
    if not bool(record.get("data_complete")):
        hard.append("DURABLE_METRICS_INCOMPLETE")
    if _int(record, "unavailable_bucket_count") > 1:
        hard.append("OBSERVATION_BUCKETS_UNAVAILABLE")
    if _int(record, "cleanup_residue") != 0 or not bool(
        record.get("rollback_verified")
    ):
        hard.append("ISOLATED_ROLLBACK_NOT_CLEAN")
    if _int(record, "execution_error_count") > 0:
        hard.append("SHADOW_EXECUTION_ERROR")
    if _int(record, "provider_calls") != 0:
        hard.append("SYNTHETIC_PROFILE_CALLED_PROVIDER")

    required_disabled = {
        "budget_mode_after_observation": "disabled",
        "budget_enforcement": "disabled",
        "compression_consumption": "disabled",
        "question_memory_consumption": "disabled",
        "principal_memory": "disabled",
        "production_observation": "NOT_RUN",
    }
    for key, expected in required_disabled.items():
        if record.get(key) != expected:
            hard.append(f"UNSAFE_FINAL_STATE_{key.upper()}")
    if bool(record.get("configuration_persisted")):
        hard.append("SHADOW_CONFIGURATION_PERSISTED")

    sample_count = _int(record, "followup_sample_count")
    if sample_count < 200:
        continue_observation.append("FOLLOWUP_SAMPLE_INSUFFICIENT")
    else:
        error_delta = _float(record, "followup_error_rate") - _float(
            record, "baseline_error_rate"
        )
        if error_delta > 0.005:
            hard.append("FOLLOWUP_ERROR_RATE_REGRESSION")
        baseline_p95 = _float(record, "baseline_p95_latency_ms")
        observed_p95 = _float(record, "followup_p95_latency_ms")
        if baseline_p95 <= 0:
            continue_observation.append("BASELINE_LATENCY_MISSING")
        elif observed_p95 > baseline_p95 * 1.20:
            hard.append("FOLLOWUP_P95_LATENCY_REGRESSION")

    if record.get("profile") != "B":
        continue_observation.append("PROFILE_B_NOT_RECORDED")
    if _int(record, "session_count") < 300:
        continue_observation.append("PROFILE_B_SESSION_SAMPLE_INSUFFICIENT")
    languages = record.get("language_sample_counts")
    if not isinstance(languages, Mapping):
        continue_observation.append("LANGUAGE_BUCKETS_MISSING")
    else:
        for language in LANGUAGES:
            if _int(languages, language) < 100:
                continue_observation.append(
                    f"LANGUAGE_SAMPLE_INSUFFICIENT_{language.upper()}"
                )
    scenarios = record.get("scenario_counts")
    if not isinstance(scenarios, Mapping):
        continue_observation.append("PROFILE_B_SCENARIOS_MISSING")
    else:
        for scenario in SCENARIOS:
            if _int(scenarios, scenario) < 1:
                continue_observation.append(
                    f"PROFILE_B_SCENARIO_MISSING_{scenario.upper()}"
                )
    if _int(record, "estimator_fallback_count") < 1:
        continue_observation.append("ESTIMATOR_FALLBACK_NOT_COVERED")
    if _int(record, "would_select_count") < 1:
        continue_observation.append("WOULD_SELECT_NOT_OBSERVED")
    if _int(record, "would_drop_count") < 1:
        continue_observation.append("WOULD_DROP_NOT_OBSERVED")
    if record.get("data_category") != "synthetic":
        hard.append("UNAPPROVED_DATA_CATEGORY")

    if hard:
        return BudgetShadowDecision("BLOCKED", tuple(sorted(set(hard))))
    if continue_observation:
        return BudgetShadowDecision(
            "CONTINUE_OBSERVATION",
            tuple(sorted(set(continue_observation))),
        )
    return BudgetShadowDecision("PASS", ())


def render_decision(decision: BudgetShadowDecision) -> tuple[str, ...]:
    if decision.status == "PASS":
        return SUCCESS_LINES
    lines = [f"BUDGET_SHADOW_STAGING={decision.status}"]
    lines.extend(f"GATE={code}" for code in decision.gate_codes)
    lines.extend(
        (
            "BUDGET_ENFORCEMENT=BLOCKED",
            "PRINCIPAL_MEMORY_SHADOW=NOT_RUN",
            "PRODUCTION_OBSERVATION=NOT_RUN",
        )
    )
    return tuple(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate the Budget Shadow Staging observation."
    )
    parser.add_argument("--observation", type=Path, default=DEFAULT_OBSERVATION)
    args = parser.parse_args(argv)
    record = json.loads(args.observation.read_text(encoding="utf-8"))
    if not isinstance(record, dict):
        raise ValueError("Budget Shadow observation must be a JSON object")
    decision = evaluate_observation(record)
    for line in render_decision(decision):
        print(line)
    return 0 if decision.status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
