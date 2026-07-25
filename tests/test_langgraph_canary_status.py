from datetime import datetime, timezone

from app.services.langgraph_canary_status import (
    CanaryThresholds,
    WorkflowCanarySnapshot,
    evaluate_canary,
)


def snapshot(**updates) -> WorkflowCanarySnapshot:
    values = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window_minutes": 60,
        "interview_rollout_percent": 1,
        "review_rollout_percent": 1,
        "interview_assigned_count": 10,
        "interview_active_count": 0,
        "interview_retrying_count": 0,
        "interview_terminal_count": 10,
        "review_assigned_count": 10,
        "review_active_count": 0,
        "review_retrying_count": 0,
        "review_terminal_count": 10,
        "review_failed_count": 0,
        "outbox_pending_count": 0,
        "oldest_outbox_age_seconds": None,
        "stale_interview_count": 0,
        "stale_review_count": 0,
        "projection_conflict_count": 0,
        "report_commit_conflict_count": 0,
        "checkpoint_row_count": 20,
        "generation_chunk_row_count": 20,
        "review_artifact_row_count": 10,
        "privacy_audit": "PASS",
        "recommendation": "HOLD",
        "reasons": ["not_evaluated"],
    }
    values.update(updates)
    return WorkflowCanarySnapshot(**values)


def test_healthy_canary_is_eligible_after_minimum_sample():
    result = evaluate_canary(snapshot())

    assert result.recommendation == "ELIGIBLE_TO_CONTINUE"
    assert result.reasons == []


def test_insufficient_sample_holds_canary():
    result = evaluate_canary(
        snapshot(interview_assigned_count=1, review_assigned_count=1),
        thresholds=CanaryThresholds(minimum_sample_size=10),
    )

    assert result.recommendation == "HOLD"
    assert result.reasons == ["insufficient_sample_size"]


def test_external_correctness_signal_rolls_back_with_stable_code():
    result = evaluate_canary(
        snapshot(),
        external_stop_signals=["acknowledged_command_loss"],
    )

    assert result.recommendation == "ROLL_BACK"
    assert result.reasons == ["acknowledged_command_loss"]


def test_privacy_or_digest_conflict_rolls_back():
    result = evaluate_canary(
        snapshot(privacy_audit="FAIL", report_commit_conflict_count=1)
    )

    assert result.recommendation == "ROLL_BACK"
    assert result.reasons == [
        "privacy_audit_failed",
        "report_commit_conflict",
    ]


def test_backlog_stale_work_and_failure_rate_hold():
    result = evaluate_canary(
        snapshot(
            oldest_outbox_age_seconds=301,
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
