from __future__ import annotations

from app.services.context_runtime import build_budget_shadow_observation
from scripts.memory_budget_shadow import (
    BudgetShadowPreflight,
    build_observation_record,
    evaluate_preflight,
    evaluate_stop_gates,
)


def _ready_preflight(**overrides):
    values = {
        "target_environment": "staging-local",
        "observation_hours": 24,
        "durable_metrics_available": True,
        "postgres_validation_passed": True,
        "knowledge_p1_ready": True,
        "long_context_gate_passed": True,
        "python_baseline_passed": True,
        "browser_baseline_passed": True,
        "staging_preflight_passed": True,
        "principal_memory_disabled": True,
        "operation_window_approved": True,
        "stop_owner_role": "memory-shadow-rollback-owner",
    }
    values.update(overrides)
    return BudgetShadowPreflight(**values)


def test_preflight_is_validate_only_and_never_changes_configuration():
    result = evaluate_preflight(_ready_preflight())

    assert result["ready"] is True
    assert result["mode"] == "VALIDATE_ONLY"
    assert result["configuration_changed"] is False
    assert result["observation_id"].startswith("budget-shadow-")


def test_preflight_fails_closed_on_consumption_or_incomplete_evidence():
    result = evaluate_preflight(
        _ready_preflight(
            durable_metrics_available=False,
            question_memory_consumption_enabled=True,
            long_term_consumption_available=True,
        )
    )

    assert result["ready"] is False
    assert "durable_metrics_unavailable" in result["failures"]
    assert "question_memory_consumption_must_remain_disabled" in result["failures"]
    assert "long_term_consumption_must_not_exist" in result["failures"]


def test_stop_gates_stop_on_hard_failure_and_do_not_expand_low_samples():
    stopped = evaluate_stop_gates(
        {
            "data_complete": True,
            "known_over_budget_provider_calls": 1,
            "followup_sample_count": 20,
        }
    )
    low_sample = evaluate_stop_gates(
        {"data_complete": True, "followup_sample_count": 199}
    )

    assert stopped["stop"] is True
    assert "known_over_budget_provider_call" in stopped["stop_reasons"]
    assert low_sample["stop"] is False
    assert low_sample["may_expand"] is False


def test_observation_record_contains_only_aggregates():
    preflight = evaluate_preflight(_ready_preflight())
    record = build_observation_record(
        preflight=preflight,
        aggregate={
            "data_complete": True,
            "followup_sample_count": 250,
            "language_sample_status": {"zh_hans": "sufficient"},
            "route_counts": {"deterministic": 250},
            "fallback_count": 0,
        },
    )

    rendered = repr(record)
    assert record["stop_gate"]["may_expand"] is True
    for forbidden in ("session_id", "principal_id", "prompt", "answer"):
        assert forbidden not in rendered


def test_hypothetical_observation_never_claims_provider_input_changed():
    observation = build_budget_shadow_observation(
        source_message_count=20,
        hypothetical_selected_count=12,
        rendered_prompt_estimate=2400,
        mandatory_current_preserved=True,
    )

    assert observation.hypothetical_dropped_count == 8
    assert observation.provider_input_unchanged is True
