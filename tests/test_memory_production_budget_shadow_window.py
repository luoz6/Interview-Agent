from copy import deepcopy

import pytest

from scripts.memory_production_budget_shadow_window import (
    WindowInputBlocked,
    build_decision_artifact,
    decide_window_action,
    validate_window_input,
)


def state_input(state="PREFLIGHT_VERIFIED"):
    return {
        "schema_version": "memory-production-budget-shadow-window-input-v1",
        "state": state,
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
        "observed_traffic_percent": 0.1,
        "warmup_duration_minutes": 0.0,
        "warmup_followup_sample_count": 0,
        "scheduled_end_reached": False,
        "manual_stop_requested": False,
    }


def test_preflight_verified_starts_warmup_without_changing_configuration():
    value = state_input()
    decision = decide_window_action(value)
    artifact = build_decision_artifact(value, decision)

    assert decision.action == "START_WARM_UP"
    assert decision.next_state == "WARM_UP"
    assert artifact["configuration_changed"] is False
    assert artifact["long_term_memory_consumption"] == "BLOCKED"


def test_warmup_requires_both_duration_and_samples_before_ramp():
    value = state_input("WARM_UP")
    duration_only = deepcopy(value)
    duration_only["warmup_duration_minutes"] = 30.0
    samples_only = deepcopy(value)
    samples_only["warmup_followup_sample_count"] = 20
    complete = deepcopy(value)
    complete["warmup_duration_minutes"] = 30.0
    complete["warmup_followup_sample_count"] = 20

    assert decide_window_action(duration_only).action == "KEEP_WARM_UP"
    assert decide_window_action(samples_only).action == "KEEP_WARM_UP"
    assert decide_window_action(complete).action == "RAMP_TO_APPROVED_CAP"


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("approval_current", False, "APPROVAL_NOT_CURRENT"),
        ("inside_approved_window", False, "APPROVAL_NOT_CURRENT"),
        ("revision_match", False, "APPROVED_REVISION_MISMATCH"),
        ("deployment_scope_verified", False, "DEPLOYMENT_SCOPE_MISMATCH"),
        ("configuration_match", False, "CONFIGURATION_DRIFT"),
        ("configuration_single_axis", False, "CONFIGURATION_DRIFT"),
        ("other_memory_axis_enabled", True, "OTHER_MEMORY_AXIS_ENABLED"),
        ("data_complete", False, "DURABLE_METRICS_INCOMPLETE"),
        ("hard_stop_count", 1, "HARD_STOP_ACTIVE"),
    ],
)
def test_runtime_safety_failure_stops_immediately(field, value, code):
    item = state_input("OBSERVING")
    item[field] = value

    decision = decide_window_action(item)

    assert decision.action == "STOP_NOW"
    assert decision.next_state == "STOPPING"
    assert code in decision.gate_codes


def test_two_missing_minute_buckets_stop():
    item = state_input("OBSERVING")
    item["max_consecutive_missing_minute_buckets"] = 2

    decision = decide_window_action(item)

    assert decision.action == "STOP_NOW"
    assert "DURABLE_METRICS_INCOMPLETE" in decision.gate_codes


def test_traffic_above_approved_cap_stops():
    item = state_input("OBSERVING")
    item["observed_traffic_percent"] = 1.01

    decision = decide_window_action(item)

    assert decision.action == "STOP_NOW"
    assert "TRAFFIC_CAP_EXCEEDED" in decision.gate_codes


def test_manual_stop_precedes_scheduled_close():
    item = state_input("OBSERVING")
    item["manual_stop_requested"] = True
    item["scheduled_end_reached"] = True

    decision = decide_window_action(item)

    assert decision.action == "STOP_NOW"
    assert decision.gate_codes == ("MANUAL_STOP",)


def test_scheduled_end_closes_even_when_healthy():
    item = state_input("OBSERVING")
    item["scheduled_end_reached"] = True

    decision = decide_window_action(item)

    assert decision.action == "CLOSE_SCHEDULED"
    assert decision.next_state == "STOPPING"


def test_closed_state_cannot_return_to_observing():
    item = state_input("CLOSED")
    item["scheduled_end_reached"] = True

    decision = decide_window_action(item)

    assert decision.action == "HOLD"
    assert decision.next_state == "CLOSED"


def test_pending_approval_holds_and_reports_missing_approval():
    item = state_input("PENDING_APPROVAL")
    item["approval_record_verified"] = False
    item["approval_current"] = False

    decision = decide_window_action(item)

    assert decision.action == "HOLD"
    assert "APPROVAL_RECORD_NOT_VERIFIED" in decision.gate_codes
    assert "APPROVAL_NOT_CURRENT" in decision.gate_codes


@pytest.mark.parametrize(
    ("mutator", "code"),
    [
        (lambda value: value.update({"state": "RUNNING"}), "WINDOW_STATE_INVALID"),
        (
            lambda value: value.update({"approved_traffic_percent": 2.0}),
            "APPROVED_TRAFFIC_PERCENT_INVALID",
        ),
        (
            lambda value: value.update({"principal_id": "private"}),
            "WINDOW_INPUT_FIELD_SET_INVALID",
        ),
    ],
)
def test_invalid_window_input_is_blocked(mutator, code):
    item = state_input()
    mutator(item)

    with pytest.raises(WindowInputBlocked) as raised:
        validate_window_input(item)

    assert code in raised.value.codes
