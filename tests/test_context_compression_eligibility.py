import pytest

from app.services.context_compression_eligibility import (
    CompressionEligibilityReason,
    ContextCompressionEligibilityPolicy,
)
from app.services.context_budget import ContextSelectionBudget
from app.services.context_selection import ContextSelectionStats
from app.services.memory_config import load_effective_memory_config


def evaluate(stats, *, target="question_conversation", policy=None):
    return (policy or ContextCompressionEligibilityPolicy()).evaluate(
        selection_stats=stats,
        target_artifact_type=target,
        source_unit_count=2,
        source_manifest_sha256="a" * 64,
    )


def demand_stats(
    *,
    required_tokens,
    selectable_tokens,
    complete_history_units=1,
    pre_dedup_required_tokens=None,
    post_dedup_required_tokens=None,
    shadow_post_dedup_required_tokens=None,
    dropped_message_count=0,
    truncated_message_count=0,
):
    pre_dedup = (
        required_tokens
        if pre_dedup_required_tokens is None
        else pre_dedup_required_tokens
    )
    source_message_count = max(2, complete_history_units * 2 + 2)
    return ContextSelectionStats(
        source_message_count=source_message_count,
        selected_message_count=max(
            0,
            source_message_count - dropped_message_count,
        ),
        dropped_message_count=dropped_message_count,
        truncated_message_count=truncated_message_count,
        source_demand_tokens=pre_dedup,
        duplicate_removed_tokens=(
            None
            if post_dedup_required_tokens is None
            else max(0, pre_dedup - post_dedup_required_tokens)
        ),
        post_dedup_demand_tokens=post_dedup_required_tokens,
        mandatory_bounded_raw_tokens=(
            0 if complete_history_units else required_tokens
        ),
        compressible_history_tokens=(
            required_tokens if complete_history_units else 0
        ),
        pre_dedup_required_tokens=pre_dedup,
        post_dedup_required_tokens=post_dedup_required_tokens,
        business_pre_loss_required_tokens=required_tokens,
        shadow_post_dedup_required_tokens=(
            shadow_post_dedup_required_tokens
        ),
        selectable_content_tokens=selectable_tokens,
        business_utilization_basis_points=round(
            required_tokens * 10_000 / selectable_tokens
        ),
        shadow_post_dedup_utilization_basis_points=(
            round(
                shadow_post_dedup_required_tokens
                * 10_000
                / selectable_tokens
            )
            if shadow_post_dedup_required_tokens is not None
            else None
        ),
        compressible_complete_history_unit_count=complete_history_units,
        retained_required_tokens=min(required_tokens, selectable_tokens),
    )


def test_short_context_without_loss_is_not_eligible():
    result = evaluate(
        ContextSelectionStats(
            source_message_count=2,
            selected_message_count=2,
        )
    )

    assert result.eligible is False
    assert result.reason is None
    assert result.dropped_count == 0
    assert result.truncated_count == 0


def test_dropped_older_conversation_is_eligible_with_stable_reason():
    result = evaluate(
        ContextSelectionStats(
            source_message_count=6,
            selected_message_count=4,
            dropped_message_count=2,
        )
    )

    assert result.eligible is True
    assert result.reason is CompressionEligibilityReason.OLDER_COMPLETE_TURN_WOULD_DROP
    assert result.policy_version == "context-compression-eligibility-v1"
    assert result.source_manifest_sha256 == "a" * 64


def test_drop_reason_remains_stronger_than_proactive_budget_reason():
    result = evaluate(
        demand_stats(
            required_tokens=9_088,
            selectable_tokens=11_360,
            dropped_message_count=2,
        )
    )

    assert result.eligible is True
    assert result.reason is (
        CompressionEligibilityReason.OLDER_COMPLETE_TURN_WOULD_DROP
    )


def test_truncated_evidence_is_eligible_with_evidence_reason():
    result = evaluate(
        ContextSelectionStats(
            source_evidence_count=2,
            selected_evidence_count=1,
            dropped_evidence_count=1,
            truncated_evidence_count=1,
        ),
        target="evidence_compression",
    )

    assert result.eligible is True
    assert result.reason is (
        CompressionEligibilityReason.EVIDENCE_REPRESENTATION_EXCESSIVE_TRUNCATION
    )
    assert result.dropped_count == 1
    assert result.truncated_count == 1


def test_missing_selection_stats_fails_safe_to_not_eligible():
    result = evaluate(None)

    assert result.eligible is False
    assert result.reason is None


@pytest.mark.parametrize(
    (
        "available_input_tokens",
        "fixed_prompt_reserve_tokens",
        "below_threshold_tokens",
        "at_threshold_tokens",
    ),
    (
        pytest.param(12_000, 640, 9_087, 9_088, id="followup-cap"),
        pytest.param(9_000, 640, 6_687, 6_688, id="smaller-window"),
    ),
)
def test_pre_loss_threshold_uses_resolved_selectable_budget_and_exact_boundary(
    available_input_tokens,
    fixed_prompt_reserve_tokens,
    below_threshold_tokens,
    at_threshold_tokens,
):
    selection_budget = ContextSelectionBudget(
        available_input_tokens=available_input_tokens,
        fixed_prompt_reserve_tokens=fixed_prompt_reserve_tokens,
        mandatory_content_floor_tokens=1,
    )
    selectable = selection_budget.selectable_content_tokens

    below = evaluate(
        demand_stats(
            required_tokens=below_threshold_tokens,
            selectable_tokens=selectable,
        )
    )
    at = evaluate(
        demand_stats(
            required_tokens=at_threshold_tokens,
            selectable_tokens=selectable,
        )
    )

    assert below.eligible is False
    assert below.reason is None
    assert at.eligible is True
    assert at.reason is CompressionEligibilityReason.APPROACHING_OPERATION_BUDGET


def test_rounded_utilization_telemetry_cannot_promote_an_ineligible_request():
    stats = demand_stats(
        required_tokens=8_003,
        selectable_tokens=10_004,
    )
    assert stats.business_utilization_basis_points == 8_000

    result = evaluate(stats)

    assert 8_003 * 10_000 < 10_004 * 8_000
    assert result.eligible is False
    assert result.reason is None


def test_threshold_requires_a_non_mandatory_complete_historical_unit():
    result = evaluate(
        demand_stats(
            required_tokens=9_088,
            selectable_tokens=11_360,
            complete_history_units=0,
        )
    )

    assert result.eligible is False
    assert result.reason is None


def test_loaded_eligibility_threshold_controls_proactive_logic():
    configs = [
        load_effective_memory_config(
            {
                "MEMORY_SELECTION_ELIGIBILITY_UTILIZATION_BASIS_POINTS": str(
                    value
                )
            }
        )
        for value in (1, 10_000)
    ]
    policies = [
        ContextCompressionEligibilityPolicy(
            eligibility_utilization_basis_points=(
                config.selection.eligibility_utilization_basis_points
            )
        )
        for config in configs
    ]
    no_loss = demand_stats(
        required_tokens=5_000,
        selectable_tokens=10_000,
    )

    results = [evaluate(no_loss, policy=policy) for policy in policies]

    assert [
        policy.eligibility_utilization_basis_points for policy in policies
    ] == [1, 10_000]
    assert [result.eligible for result in results] == [True, False]
    assert [result.reason for result in results] == [
        CompressionEligibilityReason.APPROACHING_OPERATION_BUDGET,
        None,
    ]
