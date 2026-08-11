from __future__ import annotations

import pytest

from app.services.context_budget import (
    ContextBudgetExceeded,
    ContextBudgetResolver,
    ContextSelectionBudget,
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


def test_selection_budget_uses_resolved_available_input_not_operation_cap():
    policy = OperationContextPolicy(
        operation="examiner.generate_followup",
        input_cap_tokens=12_000,
        max_output_tokens=512,
        fixed_prompt_reserve_tokens=128,
    )
    profile = ModelCapabilityRegistry().resolve(
        model="gpt-4o",
        configured_context_window_tokens=1_000,
        protocol_reserve_tokens=0,
        structured_output_reserve_tokens=0,
        safety_margin_tokens=0,
    )
    resolver = ContextBudgetResolver()
    budget = resolver.resolve(profile=profile, policy=policy)
    selection = resolver.resolve_selection_budget(
        budget=budget,
        policy=policy,
    )

    assert budget.available_input_tokens == 488
    assert selection.selectable_content_tokens == 360


def test_selection_budget_accepts_exact_mandatory_floor_boundary():
    selection = ContextSelectionBudget(
        available_input_tokens=129,
        fixed_prompt_reserve_tokens=128,
        mandatory_content_floor_tokens=1,
    )

    assert selection.selectable_content_tokens == 1


def test_selection_budget_rejects_reserve_below_mandatory_floor():
    with pytest.raises(
        ContextConfigurationError,
        match="less than the mandatory content floor",
    ):
        ContextSelectionBudget(
            available_input_tokens=128,
            fixed_prompt_reserve_tokens=128,
            mandatory_content_floor_tokens=1,
        )


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


@pytest.mark.parametrize(("prompt", "expected"), [("12345678", 8), ("123456789", 9)])
def test_rendered_prompt_guard_accepts_under_and_equal_budget(prompt, expected):
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
    measurement = RenderedPromptGuard().validate(
        prompt=prompt,
        budget=budget,
        estimator=TokenEstimatorResolution(
            ConservativeUtf8TokenEstimator(),
            "conservative_utf8",
            True,
        ),
    )

    assert measurement.estimated_input_tokens == expected


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


def dynamic_target_api():
    from app.services.context_budget import (
        DynamicCompressionTargetPolicy,
        allocate_dynamic_compression_target,
    )

    return DynamicCompressionTargetPolicy, allocate_dynamic_compression_target


def dynamic_target_policy(**changes):
    DynamicCompressionTargetPolicy, _allocator = dynamic_target_api()
    values = {
        "floor_tokens": 256,
        "source_ratio_basis_points": 2_500,
        "allowed_target_tokens": (256, 512, 1_024, 1_536, 2_000),
    }
    values.update(changes)
    return DynamicCompressionTargetPolicy(**values)


@pytest.mark.parametrize(
    ("changes", "message"),
    (
        ({"floor_tokens": 0}, "floor"),
        ({"source_ratio_basis_points": 0}, "ratio"),
        ({"source_ratio_basis_points": 10_001}, "ratio"),
        ({"allowed_target_tokens": ()}, "allowed target tiers"),
        ({"allowed_target_tokens": (0, 256)}, "positive"),
        ({"allowed_target_tokens": (256, 512, 512)}, "duplicates"),
        ({"allowed_target_tokens": (512, 256)}, "strictly increasing"),
        (
            {
                "floor_tokens": 300,
                "allowed_target_tokens": (256, 512, 1_024),
            },
            "floor",
        ),
    ),
)
def test_dynamic_compression_target_policy_fails_closed(changes, message):
    with pytest.raises(ValueError, match=message):
        dynamic_target_policy(**changes)


def test_dynamic_target_ratio_uses_integer_ceiling_before_tier_rounding():
    _policy_type, allocate = dynamic_target_api()
    policy = dynamic_target_policy(
        floor_tokens=1,
        source_ratio_basis_points=3_333,
        allowed_target_tokens=(1, 512, 513, 1_024),
    )

    target = allocate(
        source_tokens=1_537,
        policy=policy,
        policy_hard_cap_tokens=1_024,
        remaining_business_budget_tokens=1_024,
    )

    assert (1_537 * 3_333 + 9_999) // 10_000 == 513
    assert target == 513


def test_dynamic_target_rounds_required_tokens_up_to_an_allowed_tier():
    _policy_type, allocate = dynamic_target_api()

    target = allocate(
        source_tokens=2_049,
        policy=dynamic_target_policy(),
        policy_hard_cap_tokens=2_000,
        remaining_business_budget_tokens=2_000,
    )

    assert (2_049 * 2_500 + 9_999) // 10_000 == 513
    assert target == 1_024


def test_dynamic_target_never_exceeds_a_non_tier_policy_hard_cap():
    _policy_type, allocate = dynamic_target_api()

    target = allocate(
        source_tokens=20_000,
        policy=dynamic_target_policy(),
        policy_hard_cap_tokens=1_500,
        remaining_business_budget_tokens=2_000,
    )

    assert target == 1_024


def test_dynamic_target_clamps_to_largest_tier_that_fits_remaining_budget():
    _policy_type, allocate = dynamic_target_api()

    target = allocate(
        source_tokens=8_000,
        policy=dynamic_target_policy(),
        policy_hard_cap_tokens=2_000,
        remaining_business_budget_tokens=1_300,
    )

    assert target == 1_024


def test_dynamic_target_uses_resolved_available_budget_not_operation_cap():
    _policy_type, allocate = dynamic_target_api()
    profile = ModelCapabilityRegistry().resolve(
        model="gpt-4o",
        configured_context_window_tokens=1_800,
        protocol_reserve_tokens=0,
        structured_output_reserve_tokens=0,
        safety_margin_tokens=0,
    )
    operation_policy = OperationContextPolicy(
        operation="examiner.generate_followup",
        input_cap_tokens=12_000,
        max_output_tokens=512,
    )
    resolved = ContextBudgetResolver().resolve(
        profile=profile,
        policy=operation_policy,
    )

    target = allocate(
        source_tokens=20_000,
        policy=dynamic_target_policy(),
        policy_hard_cap_tokens=2_000,
        remaining_business_budget_tokens=resolved.available_input_tokens,
    )

    assert resolved.available_input_tokens == 1_288
    assert target == 1_024


@pytest.mark.parametrize(
    ("remaining_business_budget_tokens", "expected"),
    ((300, None), (512, 512)),
)
def test_dynamic_target_never_uses_an_allowed_tier_below_floor(
    remaining_business_budget_tokens,
    expected,
):
    _policy_type, allocate = dynamic_target_api()
    policy = dynamic_target_policy(
        floor_tokens=512,
        allowed_target_tokens=(256, 512, 1_024),
    )

    target = allocate(
        source_tokens=8_000,
        policy=policy,
        policy_hard_cap_tokens=2_000,
        remaining_business_budget_tokens=remaining_business_budget_tokens,
    )

    assert target == expected


@pytest.mark.parametrize(
    ("policy_hard_cap_tokens", "remaining_tokens"),
    ((2_000, 0), (2_000, 1), (2_000, 255), (255, 2_000)),
)
def test_dynamic_target_returns_none_when_no_floor_tier_fits(
    policy_hard_cap_tokens,
    remaining_tokens,
):
    _policy_type, allocate = dynamic_target_api()

    target = allocate(
        source_tokens=8_000,
        policy=dynamic_target_policy(),
        policy_hard_cap_tokens=policy_hard_cap_tokens,
        remaining_business_budget_tokens=remaining_tokens,
    )

    assert target is None


@pytest.mark.parametrize(
    (
        "source_tokens",
        "policy_hard_cap_tokens",
        "remaining_business_budget_tokens",
        "expected",
    ),
    (
        (1, 2_000, 2_000, 256),
        (2_000, 2_000, 2_000, 512),
        (2_001, 2_000, 2_000, 512),
        (4_000, 2_000, 2_000, 1_024),
        (20_000, 2_000, 2_000, 2_000),
        (20_000, 1_536, 2_000, 1_536),
        (20_000, 2_000, 600, 512),
    ),
)
def test_dynamic_target_output_invariants(
    source_tokens,
    policy_hard_cap_tokens,
    remaining_business_budget_tokens,
    expected,
):
    _policy_type, allocate = dynamic_target_api()
    policy = dynamic_target_policy()

    target = allocate(
        source_tokens=source_tokens,
        policy=policy,
        policy_hard_cap_tokens=policy_hard_cap_tokens,
        remaining_business_budget_tokens=remaining_business_budget_tokens,
    )

    assert target == expected
    assert target in policy.allowed_target_tokens
    assert target >= policy.floor_tokens
    assert target <= policy_hard_cap_tokens
    assert target <= remaining_business_budget_tokens
