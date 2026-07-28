from __future__ import annotations

import pytest

from app.services.context_budget import (
    ContextBudgetExceeded,
    ContextBudgetResolver,
    OperationContextPolicy,
    RenderedPromptGuard,
    context_enforcement_enabled,
)
from app.services.model_capabilities import (
    ContextConfigurationError,
    ModelCapabilityRegistry,
)
from app.services.token_estimation import (
    ConservativeUtf8TokenEstimator,
    TokenEstimatorResolution,
)


def test_unknown_custom_model_requires_explicit_window():
    with pytest.raises(ContextConfigurationError, match="explicit context window"):
        ModelCapabilityRegistry().resolve(
            model="proxy-model",
            custom_base_url=True,
        )


def test_budget_uses_smaller_operation_cap():
    profile = ModelCapabilityRegistry().resolve(model="gpt-4o")
    budget = ContextBudgetResolver().resolve(
        profile=profile,
        policy=OperationContextPolicy(
            operation="examiner.generate_followup",
            input_cap_tokens=12_000,
            max_output_tokens=512,
        ),
    )
    assert budget.available_input_tokens == 12_000


def test_rendered_prompt_guard_rejects_one_unit_over_budget():
    profile = ModelCapabilityRegistry().resolve(
        model="gpt-4o",
        configured_context_window_tokens=10,
        protocol_reserve_tokens=0,
        safety_margin_tokens=0,
    )
    budget = ContextBudgetResolver().resolve(
        profile=profile,
        policy=OperationContextPolicy(
            operation="test",
            input_cap_tokens=9,
            max_output_tokens=1,
        ),
    )
    estimator = TokenEstimatorResolution(
        ConservativeUtf8TokenEstimator(),
        "conservative_utf8",
        True,
    )
    with pytest.raises(ContextBudgetExceeded):
        RenderedPromptGuard().validate(
            prompt="0123456789",
            budget=budget,
            estimator=estimator,
        )


def test_rendered_prompt_guard_can_publish_measurement_before_enforcement():
    profile = ModelCapabilityRegistry().resolve(
        model="gpt-4o",
        configured_context_window_tokens=10,
        protocol_reserve_tokens=0,
        safety_margin_tokens=0,
    )
    budget = ContextBudgetResolver().resolve(
        profile=profile,
        policy=OperationContextPolicy(
            operation="test",
            input_cap_tokens=9,
            max_output_tokens=1,
        ),
    )
    guard = RenderedPromptGuard()
    measurement = guard.measure(
        prompt="0123456789",
        budget=budget,
        estimator=TokenEstimatorResolution(
            ConservativeUtf8TokenEstimator(),
            "conservative_utf8",
            True,
        ),
    )
    assert measurement.estimated_input_tokens == 10
    with pytest.raises(ContextBudgetExceeded):
        guard.enforce(measurement, budget=budget)


def test_rendered_prompt_measurement_contains_only_machine_evidence():
    profile = ModelCapabilityRegistry().resolve(
        model="gpt-4o",
        configured_context_window_tokens=100,
        protocol_reserve_tokens=0,
        safety_margin_tokens=0,
    )
    budget = ContextBudgetResolver().resolve(
        profile=profile,
        policy=OperationContextPolicy(
            operation="test",
            input_cap_tokens=99,
            max_output_tokens=1,
        ),
    )
    measurement = RenderedPromptGuard().validate(
        prompt="secret candidate answer",
        budget=budget,
        estimator=TokenEstimatorResolution(
            ConservativeUtf8TokenEstimator(),
            "conservative_utf8",
            True,
        ),
    )
    assert measurement.estimated_input_tokens == len(
        "secret candidate answer".encode("utf-8")
    )
    assert len(measurement.prompt_sha256) == 64
    assert not hasattr(measurement, "prompt")


def test_context_enforcement_defaults_off_and_is_operation_specific(monkeypatch):
    monkeypatch.delenv("CONTEXT_BUDGET_INTERVIEW_ENFORCEMENT", raising=False)
    assert context_enforcement_enabled("examiner.generate_followup") is False
    monkeypatch.setenv("CONTEXT_BUDGET_INTERVIEW_ENFORCEMENT", "true")
    assert context_enforcement_enabled("examiner.generate_followup") is True
    assert context_enforcement_enabled("report_coach.generate_report") is False
