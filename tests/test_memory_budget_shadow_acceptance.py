from __future__ import annotations

import copy
import json
from pathlib import Path

from scripts.memory_budget_shadow_acceptance import (
    SUCCESS_LINES,
    evaluate_observation,
    main,
    render_decision,
)


ROOT = Path(__file__).resolve().parents[1]
OBSERVATION = ROOT / "docs" / "memory-budget-shadow-observation.json"
ACCEPTANCE = ROOT / "docs" / "memory-budget-shadow-acceptance.md"


def _observation():
    return json.loads(OBSERVATION.read_text(encoding="utf-8"))


def test_committed_profile_b_observation_passes_with_exact_output():
    decision = evaluate_observation(_observation())

    assert decision.status == "PASS"
    assert decision.gate_codes == ()
    assert render_decision(decision) == SUCCESS_LINES


def test_non_statistical_hard_stop_blocks_even_with_low_samples():
    record = _observation()
    record["followup_sample_count"] = 10
    record["mandatory_current_content_losses"] = 1

    decision = evaluate_observation(record)

    assert decision.status == "BLOCKED"
    assert "MANDATORY_CURRENT_CONTENT_LOSS" in decision.gate_codes
    assert not any("PASS" in line for line in render_decision(decision))


def test_low_statistical_sample_continues_instead_of_pass_or_block():
    record = _observation()
    record["followup_sample_count"] = 199
    record["followup_error_rate"] = 0.5

    decision = evaluate_observation(record)

    assert decision.status == "CONTINUE_OBSERVATION"
    assert decision.gate_codes == ("FOLLOWUP_SAMPLE_INSUFFICIENT",)


def test_missing_language_or_scenario_cannot_pass():
    record = _observation()
    record["language_sample_counts"]["mixed"] = 99
    record["scenario_counts"]["fallback"] = 0

    decision = evaluate_observation(record)

    assert decision.status == "CONTINUE_OBSERVATION"
    assert "LANGUAGE_SAMPLE_INSUFFICIENT_MIXED" in decision.gate_codes
    assert "PROFILE_B_SCENARIO_MISSING_FALLBACK" in decision.gate_codes


def test_statistical_regressions_block_at_200_or_more_samples():
    error_record = _observation()
    error_record["followup_error_rate"] = 0.006
    latency_record = copy.deepcopy(_observation())
    latency_record["followup_p95_latency_ms"] = 600.001

    error = evaluate_observation(error_record)
    latency = evaluate_observation(latency_record)

    assert error.status == "BLOCKED"
    assert "FOLLOWUP_ERROR_RATE_REGRESSION" in error.gate_codes
    assert latency.status == "BLOCKED"
    assert "FOLLOWUP_P95_LATENCY_REGRESSION" in latency.gate_codes


def test_cli_prints_only_the_exact_success_lines(capsys):
    assert main(["--observation", str(OBSERVATION)]) == 0
    assert capsys.readouterr().out.strip().splitlines() == list(SUCCESS_LINES)


def test_acceptance_record_is_bound_and_keeps_enforcement_blocked():
    text = ACCEPTANCE.read_text(encoding="utf-8")

    for required in (
        "adcbe68",
        "dbc44b2",
        "ec43d7d",
        "18 passed",
        "approximately 6.65%",
        "BUDGET_SHADOW_STAGING=PASS",
        "BUDGET_ENFORCEMENT=BLOCKED",
        "PRINCIPAL_MEMORY_SHADOW=NOT_RUN",
        "PRODUCTION_OBSERVATION=NOT_RUN",
    ):
        assert required in text
    assert "PASS_FOR_PRODUCTION" not in text
    assert "postgresql://" not in text
