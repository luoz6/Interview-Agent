from copy import deepcopy
import json
from pathlib import Path

import pytest

from scripts.memory_production_budget_shadow_acceptance import (
    evaluate_observation,
    render_decision,
)
from scripts.memory_production_budget_shadow_observation import (
    sanitize_aggregate_input,
)


FIXTURE = Path(
    "tests/fixtures/memory_production_budget_shadow/pass_candidate.json"
)


def observation():
    value = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return sanitize_aggregate_input(value).artifact


def test_complete_safe_observation_passes_without_authorizing_other_memory():
    record = observation()
    decision = evaluate_observation(record)
    lines = render_decision(decision, record)

    assert decision.status == "PASS"
    assert lines == (
        "PRODUCTION_BUDGET_SHADOW=PASS",
        "OBSERVATION_WINDOW=CLOSED",
        "CONFIGURATION_RESTORED=disabled",
        "PRINCIPAL_WRITE_SHADOW_PRODUCTION=NOT_AUTHORIZED",
        "PRINCIPAL_READ_SHADOW_PRODUCTION=NOT_AUTHORIZED",
        "LONG_TERM_MEMORY_CONSUMPTION=BLOCKED",
    )


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("mandatory_current_content_losses", 1, "MANDATORY_CURRENT_CONTENT_LOSS"),
        ("provider_input_change_count", 1, "PROVIDER_INPUT_CHANGED"),
        ("known_over_budget_provider_calls", 1, "KNOWN_OVER_BUDGET_PROVIDER_CALL"),
        ("privacy_audit_hits", 1, "PRIVACY_AUDIT_HIT"),
        ("approval_current", False, "APPROVAL_NOT_CURRENT"),
        ("revision_match", False, "APPROVED_REVISION_MISMATCH"),
        ("deployment_scope_verified", False, "DEPLOYMENT_SCOPE_MISMATCH"),
        ("budget_config_conflict", True, "BUDGET_CONFIG_CONFLICT"),
        ("other_memory_axis_enabled", True, "OTHER_MEMORY_AXIS_ENABLED"),
        ("data_complete", False, "DURABLE_METRICS_INCOMPLETE"),
        ("shadow_execution_error_count", 1, "SHADOW_EXECUTION_ERROR"),
        (
            "deterministic_interview_regression_count",
            1,
            "DETERMINISTIC_INTERVIEW_REGRESSION",
        ),
        ("configuration_drift_count", 1, "CONFIGURATION_DRIFT"),
        ("rollback_verified", False, "ROLLBACK_NOT_VERIFIED"),
        ("configuration_restored", False, "CONFIGURATION_NOT_RESTORED"),
    ],
)
def test_hard_stop_inputs_block(field, value, code):
    record = observation()
    record[field] = value

    decision = evaluate_observation(record)

    assert decision.status == "BLOCKED"
    assert code in decision.gate_codes
    assert not any("=PASS" in line for line in render_decision(decision, record))


def test_traffic_overshoot_blocks():
    record = observation()
    record["observed_traffic_percent_max"] = 1.01

    decision = evaluate_observation(record)

    assert decision.status == "BLOCKED"
    assert "TRAFFIC_CAP_EXCEEDED" in decision.gate_codes


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("observed_traffic_percent_max", 0.0, "OBSERVED_TRAFFIC_ZERO"),
        ("warmup_duration_minutes", 29.0, "WARMUP_DURATION_INSUFFICIENT"),
        ("warmup_followup_sample_count", 19, "WARMUP_SAMPLE_INSUFFICIENT"),
        ("observation_window_duration_hours", 23.9, "OBSERVATION_WINDOW_TOO_SHORT"),
        ("followup_sample_count", 199, "FOLLOWUP_SAMPLE_INSUFFICIENT"),
        ("control_sample_count", 0, "CONTROL_SAMPLE_MISSING"),
        ("shadow_sample_count", 0, "SHADOW_SAMPLE_MISSING"),
        ("baseline_p95_latency_ms", 0.0, "BASELINE_LATENCY_MISSING"),
    ],
)
def test_insufficient_evidence_requires_a_new_window(field, value, code):
    record = observation()
    record[field] = value

    decision = evaluate_observation(record)
    lines = render_decision(decision, record)

    assert decision.status == "CONTINUE_OBSERVATION"
    assert code in decision.gate_codes
    assert "NEW_APPROVAL_WINDOW_REQUIRED=true" in lines
    assert not any("=PASS" in line for line in lines)


def test_error_and_latency_regressions_apply_after_two_hundred_samples():
    error = observation()
    error["observed_error_rate"] = 0.016
    latency = observation()
    latency["observed_p95_latency_ms"] = 601.0

    error_decision = evaluate_observation(error)
    latency_decision = evaluate_observation(latency)

    assert "FOLLOWUP_ERROR_RATE_REGRESSION" in error_decision.gate_codes
    assert "FOLLOWUP_P95_LATENCY_REGRESSION" in latency_decision.gate_codes


def test_failed_restore_is_reported_truthfully():
    record = observation()
    record["configuration_restored"] = False

    lines = render_decision(evaluate_observation(record), record)

    assert "CONFIGURATION_RESTORED=NOT_VERIFIED" in lines
    assert "CONFIGURATION_RESTORED=disabled" not in lines
