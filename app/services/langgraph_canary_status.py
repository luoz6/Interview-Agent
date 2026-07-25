from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field


Recommendation = Literal["HOLD", "ROLL_BACK", "ELIGIBLE_TO_CONTINUE"]
ExternalStopSignal = Literal[
    "acknowledged_command_loss",
    "duplicate_business_projection",
    "public_version_regression",
    "unknown_graph_version",
]


class WorkflowCanarySnapshot(BaseModel):
    schema_version: Literal["langgraph-canary-v1"] = "langgraph-canary-v1"
    generated_at: str
    window_minutes: int = Field(ge=1)
    interview_rollout_percent: int = Field(ge=0, le=100)
    review_rollout_percent: int = Field(ge=0, le=100)
    interview_assigned_count: int = Field(ge=0)
    interview_active_count: int = Field(ge=0)
    interview_retrying_count: int = Field(ge=0)
    interview_terminal_count: int = Field(ge=0)
    review_assigned_count: int = Field(ge=0)
    review_active_count: int = Field(ge=0)
    review_retrying_count: int = Field(ge=0)
    review_terminal_count: int = Field(ge=0)
    review_failed_count: int = Field(ge=0)
    outbox_pending_count: int = Field(ge=0)
    oldest_outbox_age_seconds: float | None = Field(default=None, ge=0)
    stale_interview_count: int = Field(ge=0)
    stale_review_count: int = Field(ge=0)
    projection_conflict_count: int = Field(ge=0)
    report_commit_conflict_count: int = Field(ge=0)
    checkpoint_row_count: int = Field(ge=0)
    generation_chunk_row_count: int = Field(ge=0)
    review_artifact_row_count: int = Field(ge=0)
    privacy_audit: Literal["PASS", "FAIL"]
    recommendation: Recommendation
    reasons: list[str]


class CanaryThresholds(BaseModel):
    minimum_sample_size: int = Field(default=10, ge=1)
    max_oldest_outbox_age_seconds: float = Field(default=300, gt=0)
    max_stale_interview_count: int = Field(default=0, ge=0)
    max_stale_review_count: int = Field(default=0, ge=0)
    max_projection_conflict_count: int = Field(default=5, ge=0)
    max_review_failure_rate: float = Field(default=0.2, ge=0, le=1)


def evaluate_canary(
    snapshot: WorkflowCanarySnapshot,
    *,
    thresholds: CanaryThresholds | None = None,
    external_stop_signals: list[ExternalStopSignal] | None = None,
) -> WorkflowCanarySnapshot:
    thresholds = thresholds or CanaryThresholds()
    stop_signals = sorted(set(external_stop_signals or []))
    rollback_reasons = list(stop_signals)
    if snapshot.privacy_audit != "PASS":
        rollback_reasons.append("privacy_audit_failed")
    if snapshot.report_commit_conflict_count:
        rollback_reasons.append("report_commit_conflict")
    if rollback_reasons:
        return snapshot.model_copy(
            update={
                "recommendation": "ROLL_BACK",
                "reasons": sorted(set(rollback_reasons)),
            }
        )

    hold_reasons: list[str] = []
    sample_size = (
        snapshot.interview_assigned_count + snapshot.review_assigned_count
    )
    if sample_size < thresholds.minimum_sample_size:
        hold_reasons.append("insufficient_sample_size")
    if (
        snapshot.oldest_outbox_age_seconds is not None
        and snapshot.oldest_outbox_age_seconds
        > thresholds.max_oldest_outbox_age_seconds
    ):
        hold_reasons.append("outbox_backlog_too_old")
    if snapshot.stale_interview_count > thresholds.max_stale_interview_count:
        hold_reasons.append("stale_interview_work")
    if snapshot.stale_review_count > thresholds.max_stale_review_count:
        hold_reasons.append("stale_review_work")
    if (
        snapshot.projection_conflict_count
        > thresholds.max_projection_conflict_count
    ):
        hold_reasons.append("projection_conflict_rate_high")
    if snapshot.review_terminal_count:
        failure_rate = (
            snapshot.review_failed_count / snapshot.review_terminal_count
        )
        if failure_rate > thresholds.max_review_failure_rate:
            hold_reasons.append("review_failure_rate_high")
    if hold_reasons:
        return snapshot.model_copy(
            update={
                "recommendation": "HOLD",
                "reasons": sorted(set(hold_reasons)),
            }
        )
    return snapshot.model_copy(
        update={"recommendation": "ELIGIBLE_TO_CONTINUE", "reasons": []}
    )


class PostgresLangGraphCanaryStatusService:
    def __init__(self, *, dsn: str, table_prefix: str) -> None:
        self.dsn = dsn
        self.table_prefix = table_prefix

    def snapshot(
        self,
        *,
        window_minutes: int,
        interview_rollout_percent: int,
        review_rollout_percent: int,
    ) -> WorkflowCanarySnapshot:
        if window_minutes < 1:
            raise ValueError("window_minutes must be positive")
        psycopg2, sql = self._import_psycopg2()
        names = {
            key: sql.Identifier(f"{self.table_prefix}_{suffix}")
            for key, suffix in {
                "sessions": "sessions",
                "commands": "workflow_commands",
                "generations": "generations",
                "attempts": "generation_attempts",
                "chunks": "generation_chunks",
                "outbox": "runtime_outbox",
                "jobs": "report_jobs",
                "runs": "review_runs",
                "artifacts": "review_artifacts",
            }.items()
        }
        with psycopg2.connect(self.dsn) as connection:
            with connection.cursor() as cursor:
                values = {
                    "interview_assigned_count": self._scalar(
                        cursor,
                        sql.SQL(
                            "SELECT COUNT(*) FROM {sessions} WHERE workflow_engine = 'langgraph-v1' AND started_at >= NOW() - (%s * INTERVAL '1 minute')"
                        ).format(**names),
                        (window_minutes,),
                    ),
                    "interview_active_count": self._scalar(
                        cursor,
                        sql.SQL(
                            "SELECT COUNT(*) FROM {sessions} WHERE workflow_engine = 'langgraph-v1' AND status = 'active'"
                        ).format(**names),
                    ),
                    "interview_retrying_count": self._scalar(
                        cursor,
                        sql.SQL(
                            "SELECT COUNT(DISTINCT generations.generation_id) FROM {generations} AS generations JOIN {attempts} AS attempts ON attempts.generation_id = generations.generation_id WHERE generations.status = 'running' AND attempts.status = 'failed'"
                        ).format(**names),
                    ),
                    "interview_terminal_count": self._scalar(
                        cursor,
                        sql.SQL(
                            "SELECT COUNT(*) FROM {sessions} WHERE workflow_engine = 'langgraph-v1' AND status = 'finished' AND finished_at >= NOW() - (%s * INTERVAL '1 minute')"
                        ).format(**names),
                        (window_minutes,),
                    ),
                    "review_assigned_count": self._scalar(
                        cursor,
                        sql.SQL(
                            "SELECT COUNT(*) FROM {jobs} WHERE review_engine = 'langgraph-review-v1' AND queued_at >= NOW() - (%s * INTERVAL '1 minute')"
                        ).format(**names),
                        (window_minutes,),
                    ),
                    "review_active_count": self._scalar(
                        cursor,
                        sql.SQL(
                            "SELECT COUNT(*) FROM {jobs} WHERE review_engine = 'langgraph-review-v1' AND status IN ('queued', 'running', 'retrying')"
                        ).format(**names),
                    ),
                    "review_retrying_count": self._scalar(
                        cursor,
                        sql.SQL(
                            "SELECT COUNT(*) FROM {jobs} WHERE review_engine = 'langgraph-review-v1' AND status = 'retrying'"
                        ).format(**names),
                    ),
                    "review_terminal_count": self._scalar(
                        cursor,
                        sql.SQL(
                            "SELECT COUNT(*) FROM {jobs} WHERE review_engine = 'langgraph-review-v1' AND status IN ('completed', 'failed') AND finished_at >= NOW() - (%s * INTERVAL '1 minute')"
                        ).format(**names),
                        (window_minutes,),
                    ),
                    "review_failed_count": self._scalar(
                        cursor,
                        sql.SQL(
                            "SELECT COUNT(*) FROM {jobs} WHERE review_engine = 'langgraph-review-v1' AND status = 'failed' AND finished_at >= NOW() - (%s * INTERVAL '1 minute')"
                        ).format(**names),
                        (window_minutes,),
                    ),
                    "outbox_pending_count": self._scalar(
                        cursor,
                        sql.SQL(
                            "SELECT COUNT(*) FROM {outbox} WHERE status IN ('pending', 'processing')"
                        ).format(**names),
                    ),
                    "stale_interview_count": self._scalar(
                        cursor,
                        sql.SQL(
                            "SELECT COUNT(*) FROM {sessions} WHERE workflow_engine = 'langgraph-v1' AND status = 'active' AND updated_at < NOW() - (%s * INTERVAL '1 minute')"
                        ).format(**names),
                        (window_minutes,),
                    ),
                    "stale_review_count": self._scalar(
                        cursor,
                        sql.SQL(
                            "SELECT COUNT(*) FROM {runs} WHERE status IN ('pending', 'running', 'waiting') AND updated_at < NOW() - (%s * INTERVAL '1 minute')"
                        ).format(**names),
                        (window_minutes,),
                    ),
                    "projection_conflict_count": self._scalar(
                        cursor,
                        sql.SQL(
                            "SELECT COUNT(*) FROM {commands} WHERE status = 'conflict' AND completed_at >= NOW() - (%s * INTERVAL '1 minute')"
                        ).format(**names),
                        (window_minutes,),
                    ),
                    "report_commit_conflict_count": self._scalar(
                        cursor,
                        sql.SQL(
                            "SELECT COUNT(*) FROM {runs} WHERE error_code = 'report_commit_conflict' AND updated_at >= NOW() - (%s * INTERVAL '1 minute')"
                        ).format(**names),
                        (window_minutes,),
                    ),
                    "generation_chunk_row_count": self._scalar(
                        cursor,
                        sql.SQL("SELECT COUNT(*) FROM {chunks}").format(
                            **names
                        ),
                    ),
                    "review_artifact_row_count": self._scalar(
                        cursor,
                        sql.SQL("SELECT COUNT(*) FROM {artifacts}").format(
                            **names
                        ),
                    ),
                }
                cursor.execute(
                    sql.SQL(
                        "SELECT EXTRACT(EPOCH FROM (NOW() - MIN(created_at))) FROM {outbox} WHERE status IN ('pending', 'processing')"
                    ).format(**names)
                )
                oldest = cursor.fetchone()[0]
                cursor.execute("SELECT to_regclass('checkpoints')")
                checkpoint_table = cursor.fetchone()[0]
                checkpoint_count = 0
                if checkpoint_table:
                    cursor.execute("SELECT COUNT(*) FROM checkpoints")
                    checkpoint_count = int(cursor.fetchone()[0])
        return WorkflowCanarySnapshot(
            generated_at=datetime.now(timezone.utc).isoformat(),
            window_minutes=window_minutes,
            interview_rollout_percent=interview_rollout_percent,
            review_rollout_percent=review_rollout_percent,
            oldest_outbox_age_seconds=(
                max(0.0, float(oldest)) if oldest is not None else None
            ),
            checkpoint_row_count=checkpoint_count,
            privacy_audit="PASS",
            recommendation="HOLD",
            reasons=["not_evaluated"],
            **values,
        )

    @staticmethod
    def _scalar(cursor, statement, params=None) -> int:
        cursor.execute(statement, params)
        return int(cursor.fetchone()[0])

    @staticmethod
    def _import_psycopg2():
        try:
            import psycopg2
            from psycopg2 import sql
        except ImportError as exc:
            raise RuntimeError("psycopg2-binary is required") from exc
        return psycopg2, sql
