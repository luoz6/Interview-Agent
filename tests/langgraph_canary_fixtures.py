from datetime import datetime, timezone

from app.services.langgraph_canary_status import WorkflowCanarySnapshot


def canary_snapshot(**updates) -> WorkflowCanarySnapshot:
    values = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "observed_since": "2026-07-25T00:00:00+00:00",
        "window_seconds": 3600,
        "phase": "joint",
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
        "outbox_retrying_count": 0,
        "outbox_running_count": 0,
        "oldest_unfinished_outbox_age_seconds": None,
        "stale_interview_count": 0,
        "stale_review_count": 0,
        "command_conflict_count": 0,
        "projection_divergence_count": 0,
        "report_commit_conflict_count": 0,
        "checkpoint_row_count": 20,
        "generation_chunk_row_count": 20,
        "review_artifact_row_count": 10,
        "review_effect_row_count": 10,
        "privacy_audit": "PASS",
        "recommendation": "HOLD",
        "reasons": ["not_evaluated"],
    }
    values.update(updates)
    return WorkflowCanarySnapshot(**values)
