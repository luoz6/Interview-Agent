import pytest
from pydantic import ValidationError

from app.services.langgraph_canary_status import (
    CanaryThresholds,
    evaluate_canary,
)
from tests.langgraph_canary_fixtures import canary_snapshot as snapshot


def test_healthy_joint_canary_is_eligible_after_independent_samples():
    result = evaluate_canary(snapshot())

    assert result.schema_version == "langgraph-canary-v2"
    assert result.recommendation == "ELIGIBLE_TO_CONTINUE"
    assert result.reasons == []


def test_interview_phase_requires_interview_sample_only():
    result = evaluate_canary(
        snapshot(
            phase="interview",
            interview_rollout_percent=1,
            review_rollout_percent=0,
            interview_assigned_count=9,
            review_assigned_count=100,
        )
    )

    assert result.recommendation == "HOLD"
    assert result.reasons == ["insufficient_interview_sample"]


def test_review_phase_requires_review_sample_only():
    result = evaluate_canary(
        snapshot(
            phase="review",
            interview_rollout_percent=0,
            review_rollout_percent=1,
            interview_assigned_count=100,
            review_assigned_count=9,
        )
    )

    assert result.recommendation == "HOLD"
    assert result.reasons == ["insufficient_review_sample"]


def test_joint_phase_requires_both_samples():
    result = evaluate_canary(
        snapshot(interview_assigned_count=9, review_assigned_count=9)
    )

    assert result.recommendation == "HOLD"
    assert result.reasons == [
        "insufficient_interview_sample",
        "insufficient_review_sample",
    ]


def test_zero_zero_phase_evaluates_health_without_assignment_sample():
    result = evaluate_canary(
        snapshot(
            phase="baseline",
            interview_rollout_percent=0,
            review_rollout_percent=0,
            interview_assigned_count=0,
            review_assigned_count=0,
        )
    )

    assert result.recommendation == "ELIGIBLE_TO_CONTINUE"


def test_phase_rollout_pair_is_fixed_for_initial_canary():
    with pytest.raises(ValidationError, match="requires rollout pair 1/0"):
        snapshot(
            phase="interview",
            interview_rollout_percent=1,
            review_rollout_percent=1,
        )


def test_external_correctness_signal_rolls_back_with_stable_code():
    result = evaluate_canary(
        snapshot(),
        external_stop_signals=["acknowledged_command_loss"],
    )

    assert result.recommendation == "ROLL_BACK"
    assert result.reasons == ["acknowledged_command_loss"]


def test_privacy_projection_effect_and_commit_conflicts_roll_back():
    result = evaluate_canary(
        snapshot(
            privacy_audit="FAIL",
            projection_divergence_count=1,
            report_commit_conflict_count=1,
            review_effect_conflict_count=1,
        )
    )

    assert result.recommendation == "ROLL_BACK"
    assert result.reasons == [
        "privacy_audit_failed",
        "projection_conflict",
        "report_commit_conflict",
        "review_effect_conflict",
    ]


def test_command_conflict_is_informational_not_projection_divergence():
    result = evaluate_canary(snapshot(command_conflict_count=20))

    assert result.recommendation == "ELIGIBLE_TO_CONTINUE"


def test_context_configuration_and_provider_overflow_roll_back():
    result = evaluate_canary(
        snapshot(
            context_configuration_error_count=1,
            provider_context_overflow_count=1,
        )
    )

    assert result.recommendation == "ROLL_BACK"
    assert result.reasons == [
        "context_configuration_error",
        "provider_context_overflow",
    ]


def test_context_budget_usage_and_estimator_incidents_hold():
    result = evaluate_canary(
        snapshot(
            context_budget_exceeded_count=1,
            context_estimator_unavailable_count=1,
            provider_usage_missing_count=1,
        )
    )

    assert result.recommendation == "HOLD"
    assert result.reasons == [
        "context_budget_exceeded",
        "context_estimator_unavailable",
        "provider_usage_missing",
    ]


def test_expected_context_shrink_and_fallback_are_informational():
    result = evaluate_canary(
        snapshot(
            context_estimator_fallback_count=2,
            context_deterministic_shrink_count=3,
            context_message_truncated_count=4,
            context_evidence_truncated_count=5,
            report_microbatch_budget_route_count=6,
        )
    )

    assert result.recommendation == "ELIGIBLE_TO_CONTINUE"


def test_ownership_loss_and_expiry_hold():
    result = evaluate_canary(
        snapshot(
            workflow_thread_lock_lost_count=1,
            generation_lease_lost_count=1,
            fenced_write_rejected_count=1,
            report_lease_lost_count=1,
            expired_running_outbox_lease_count=1,
            expired_generation_lease_count=1,
            expired_report_lease_count=1,
            expired_review_effect_claim_count=1,
        )
    )

    assert result.recommendation == "HOLD"
    assert result.reasons == ["ownership_anomaly"]


def test_running_nonexpired_effect_is_informational():
    result = evaluate_canary(snapshot(running_review_effect_count=5))

    assert result.recommendation == "ELIGIBLE_TO_CONTINUE"


def test_busy_above_threshold_holds_without_becoming_rollback():
    result = evaluate_canary(
        snapshot(workflow_thread_busy_count=2, review_effect_busy_count=2),
        thresholds=CanaryThresholds(
            max_workflow_thread_busy_count=1,
            max_review_effect_busy_count=1,
        ),
    )

    assert result.recommendation == "HOLD"
    assert result.reasons == ["review_effect_busy", "workflow_thread_busy"]


def test_backlog_stale_work_and_failure_rate_hold():
    result = evaluate_canary(
        snapshot(
            oldest_unfinished_outbox_age_seconds=301,
            stale_interview_count=1,
            stale_review_count=1,
            review_failed_count=3,
        )
    )

    assert result.recommendation == "HOLD"
    assert set(result.reasons) == {
        "outbox_backlog_too_old",
        "review_failure_rate_high",
        "stale_interview_work",
        "stale_review_work",
    }


def test_legacy_sample_threshold_sets_both_workflow_minima():
    result = evaluate_canary(
        snapshot(interview_assigned_count=4, review_assigned_count=4),
        thresholds=CanaryThresholds(minimum_sample_size=5),
    )

    assert result.reasons == [
        "insufficient_interview_sample",
        "insufficient_review_sample",
    ]
