from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.services.postgres_connections import (
    ConnectionProvider,
    DirectPsycopg2ConnectionProvider,
)
from app.services.postgres_identifiers import validate_runtime_table_prefix


Recommendation = Literal["HOLD", "ROLL_BACK", "ELIGIBLE_TO_CONTINUE"]
CanaryPhase = Literal[
    "baseline",
    "interview",
    "interview_drain",
    "review",
    "review_drain",
    "joint",
    "final_drain",
]
ExternalStopSignal = Literal[
    "acknowledged_command_loss",
    "duplicate_business_projection",
    "public_version_regression",
    "unknown_graph_version",
]


class WorkflowCanarySnapshot(BaseModel):
    schema_version: Literal["langgraph-canary-v2"] = "langgraph-canary-v2"
    generated_at: str
    observed_since: str = "unspecified"
    window_seconds: int = Field(default=3600, ge=0)
    phase: CanaryPhase = "joint"
    interview_rollout_percent: int = Field(ge=0, le=100)
    review_rollout_percent: int = Field(ge=0, le=100)
    interview_assigned_count: int = Field(default=0, ge=0)
    interview_active_count: int = Field(default=0, ge=0)
    interview_retrying_count: int = Field(default=0, ge=0)
    interview_terminal_count: int = Field(default=0, ge=0)
    review_assigned_count: int = Field(default=0, ge=0)
    review_active_count: int = Field(default=0, ge=0)
    review_retrying_count: int = Field(default=0, ge=0)
    review_terminal_count: int = Field(default=0, ge=0)
    review_failed_count: int = Field(default=0, ge=0)
    outbox_pending_count: int = Field(default=0, ge=0)
    outbox_retrying_count: int = Field(default=0, ge=0)
    outbox_running_count: int = Field(default=0, ge=0)
    oldest_outbox_age_seconds: float | None = Field(default=None, ge=0)
    oldest_unfinished_outbox_age_seconds: float | None = Field(
        default=None, ge=0
    )
    expired_running_outbox_lease_count: int = Field(default=0, ge=0)
    stale_interview_count: int = Field(default=0, ge=0)
    stale_review_count: int = Field(default=0, ge=0)
    command_conflict_count: int = Field(default=0, ge=0)
    projection_divergence_count: int = Field(default=0, ge=0)
    report_commit_conflict_count: int = Field(default=0, ge=0)
    checkpoint_row_count: int = Field(default=0, ge=0)
    generation_chunk_row_count: int = Field(default=0, ge=0)
    review_artifact_row_count: int = Field(default=0, ge=0)
    review_effect_row_count: int = Field(default=0, ge=0)
    workflow_thread_busy_count: int = Field(default=0, ge=0)
    workflow_thread_lock_lost_count: int = Field(default=0, ge=0)
    generation_lease_lost_count: int = Field(default=0, ge=0)
    fenced_write_rejected_count: int = Field(default=0, ge=0)
    report_lease_lost_count: int = Field(default=0, ge=0)
    review_effect_busy_count: int = Field(default=0, ge=0)
    review_effect_conflict_count: int = Field(default=0, ge=0)
    expired_generation_lease_count: int = Field(default=0, ge=0)
    expired_report_lease_count: int = Field(default=0, ge=0)
    running_review_effect_count: int = Field(default=0, ge=0)
    expired_review_effect_claim_count: int = Field(default=0, ge=0)
    context_budget_exceeded_count: int = Field(default=0, ge=0)
    context_configuration_error_count: int = Field(default=0, ge=0)
    context_estimator_unavailable_count: int = Field(default=0, ge=0)
    context_estimator_fallback_count: int = Field(default=0, ge=0)
    context_deterministic_shrink_count: int = Field(default=0, ge=0)
    context_message_truncated_count: int = Field(default=0, ge=0)
    context_evidence_truncated_count: int = Field(default=0, ge=0)
    report_microbatch_budget_route_count: int = Field(default=0, ge=0)
    provider_usage_missing_count: int = Field(default=0, ge=0)
    provider_context_overflow_count: int = Field(default=0, ge=0)
    privacy_audit: Literal["PASS", "FAIL"] = "PASS"
    recommendation: Recommendation = "HOLD"
    reasons: list[str] = Field(default_factory=list)

    @property
    def window_minutes(self) -> int:
        return max(1, (self.window_seconds + 59) // 60)

    @model_validator(mode="after")
    def validate_phase_rollout(self):
        expected = {
            "baseline": (0, 0),
            "interview": (1, 0),
            "interview_drain": (0, 0),
            "review": (0, 1),
            "review_drain": (0, 0),
            "joint": (1, 1),
            "final_drain": (0, 0),
        }[self.phase]
        actual = (
            self.interview_rollout_percent,
            self.review_rollout_percent,
        )
        if actual != expected:
            raise ValueError(
                f"phase {self.phase} requires rollout pair "
                f"{expected[0]}/{expected[1]}"
            )
        return self


class CanaryThresholds(BaseModel):
    minimum_interview_sample: int = Field(default=10, ge=0)
    minimum_review_sample: int = Field(default=10, ge=0)
    max_oldest_outbox_age_seconds: float = Field(default=300, gt=0)
    max_stale_interview_count: int = Field(default=0, ge=0)
    max_stale_review_count: int = Field(default=0, ge=0)
    max_review_failure_rate: float = Field(default=0.2, ge=0, le=1)
    max_workflow_thread_busy_count: int = Field(default=0, ge=0)
    max_review_effect_busy_count: int = Field(default=0, ge=0)
    max_ownership_anomaly_count: int = Field(default=0, ge=0)
    lease_expiry_grace_seconds: int = Field(default=30, ge=0)
    minimum_sample_size: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def support_legacy_sample_threshold(self):
        if self.minimum_sample_size is not None:
            self.minimum_interview_sample = self.minimum_sample_size
            self.minimum_review_sample = self.minimum_sample_size
        return self


def evaluate_canary(
    snapshot: WorkflowCanarySnapshot,
    *,
    thresholds: CanaryThresholds | None = None,
    external_stop_signals: list[ExternalStopSignal] | None = None,
) -> WorkflowCanarySnapshot:
    thresholds = thresholds or CanaryThresholds()
    rollback_reasons = sorted(set(external_stop_signals or []))
    if snapshot.privacy_audit != "PASS":
        rollback_reasons.append("privacy_audit_failed")
    if snapshot.projection_divergence_count:
        rollback_reasons.append("projection_conflict")
    if snapshot.report_commit_conflict_count:
        rollback_reasons.append("report_commit_conflict")
    if snapshot.review_effect_conflict_count:
        rollback_reasons.append("review_effect_conflict")
    if snapshot.context_configuration_error_count:
        rollback_reasons.append("context_configuration_error")
    if snapshot.provider_context_overflow_count:
        rollback_reasons.append("provider_context_overflow")
    if rollback_reasons:
        return snapshot.model_copy(
            update={
                "recommendation": "ROLL_BACK",
                "reasons": sorted(set(rollback_reasons)),
            }
        )

    hold_reasons: list[str] = []
    if snapshot.phase in {"interview", "joint"} and (
        snapshot.interview_assigned_count
        < thresholds.minimum_interview_sample
    ):
        hold_reasons.append("insufficient_interview_sample")
    if snapshot.phase in {"review", "joint"} and (
        snapshot.review_assigned_count < thresholds.minimum_review_sample
    ):
        hold_reasons.append("insufficient_review_sample")
    if not snapshot.observed_since.strip():
        hold_reasons.append("missing_phase_start")
    oldest_outbox_age = (
        snapshot.oldest_unfinished_outbox_age_seconds
        if snapshot.oldest_unfinished_outbox_age_seconds is not None
        else snapshot.oldest_outbox_age_seconds
    )
    if (
        oldest_outbox_age is not None
        and oldest_outbox_age > thresholds.max_oldest_outbox_age_seconds
    ):
        hold_reasons.append("outbox_backlog_too_old")
    if snapshot.stale_interview_count > thresholds.max_stale_interview_count:
        hold_reasons.append("stale_interview_work")
    if snapshot.stale_review_count > thresholds.max_stale_review_count:
        hold_reasons.append("stale_review_work")
    if (
        snapshot.workflow_thread_busy_count
        > thresholds.max_workflow_thread_busy_count
    ):
        hold_reasons.append("workflow_thread_busy")
    if (
        snapshot.review_effect_busy_count
        > thresholds.max_review_effect_busy_count
    ):
        hold_reasons.append("review_effect_busy")
    ownership_anomaly_count = sum(
        (
            snapshot.workflow_thread_lock_lost_count,
            snapshot.generation_lease_lost_count,
            snapshot.fenced_write_rejected_count,
            snapshot.report_lease_lost_count,
            snapshot.expired_running_outbox_lease_count,
            snapshot.expired_generation_lease_count,
            snapshot.expired_report_lease_count,
            snapshot.expired_review_effect_claim_count,
        )
    )
    if ownership_anomaly_count > thresholds.max_ownership_anomaly_count:
        hold_reasons.append("ownership_anomaly")
    if snapshot.review_terminal_count:
        failure_rate = (
            snapshot.review_failed_count / snapshot.review_terminal_count
        )
        if failure_rate > thresholds.max_review_failure_rate:
            hold_reasons.append("review_failure_rate_high")
    if snapshot.context_budget_exceeded_count:
        hold_reasons.append("context_budget_exceeded")
    if snapshot.context_estimator_unavailable_count:
        hold_reasons.append("context_estimator_unavailable")
    if snapshot.provider_usage_missing_count:
        hold_reasons.append("provider_usage_missing")
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
    _SIGNAL_FIELDS = {
        "workflow_thread_busy": "workflow_thread_busy_count",
        "workflow_thread_lock_lost": "workflow_thread_lock_lost_count",
        "generation_lease_lost": "generation_lease_lost_count",
        "fenced_write_rejected": "fenced_write_rejected_count",
        "projection_conflict": "projection_divergence_count",
        "report_lease_lost": "report_lease_lost_count",
        "review_effect_busy": "review_effect_busy_count",
        "review_effect_conflict": "review_effect_conflict_count",
        "report_commit_conflict": "report_commit_conflict_count",
        "context_budget_exceeded": "context_budget_exceeded_count",
        "context_configuration_error": "context_configuration_error_count",
        "context_estimator_unavailable": "context_estimator_unavailable_count",
        "context_estimator_fallback": "context_estimator_fallback_count",
        "context_deterministic_shrink": "context_deterministic_shrink_count",
        "context_message_truncated": "context_message_truncated_count",
        "context_evidence_truncated": "context_evidence_truncated_count",
        "report_microbatch_budget_route": "report_microbatch_budget_route_count",
        "provider_usage_missing": "provider_usage_missing_count",
        "provider_context_overflow": "provider_context_overflow_count",
    }

    def __init__(
        self,
        *,
        dsn: str | None = None,
        connection_provider: ConnectionProvider | None = None,
        table_prefix: str,
    ) -> None:
        validate_runtime_table_prefix(table_prefix)
        if connection_provider is None:
            if not dsn:
                raise ValueError("dsn or connection_provider is required")
            connection_provider = DirectPsycopg2ConnectionProvider(dsn)
            self._provider_is_owned = True
        else:
            self._provider_is_owned = False
        self.dsn = dsn or ""
        self._connection_provider = connection_provider
        self.table_prefix = table_prefix

    def snapshot(
        self,
        *,
        window_minutes: int = 60,
        observed_since: datetime | None = None,
        phase: CanaryPhase | None = None,
        interview_rollout_percent: int,
        review_rollout_percent: int,
        lease_expiry_grace_seconds: int = 30,
    ) -> WorkflowCanarySnapshot:
        if window_minutes < 1:
            raise ValueError("window_minutes must be positive")
        if lease_expiry_grace_seconds < 0:
            raise ValueError("lease expiry grace must be non-negative")
        now = datetime.now(timezone.utc)
        observed_since = observed_since or (
            now - timedelta(minutes=window_minutes)
        )
        if observed_since.tzinfo is None:
            raise ValueError("observed_since must be timezone-aware")
        phase = phase or self._phase_for_pair(
            interview_rollout_percent,
            review_rollout_percent,
        )
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
                "effects": "review_effects",
                "signals": "runtime_signal_buckets",
            }.items()
        }
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                values = self._business_counts(
                    cursor,
                    sql,
                    names,
                    observed_since=observed_since,
                    grace_seconds=lease_expiry_grace_seconds,
                )
                values.update(
                    self._signal_counts(
                        cursor,
                        sql,
                        names,
                        observed_since=observed_since,
                    )
                )
                oldest = self._oldest_unfinished_outbox_age(
                    cursor, sql, names
                )
                checkpoint_count = self._checkpoint_row_count(cursor)
        window_seconds = max(
            0,
            int((now - observed_since.astimezone(timezone.utc)).total_seconds()),
        )
        return WorkflowCanarySnapshot(
            generated_at=now.isoformat(),
            observed_since=observed_since.astimezone(timezone.utc).isoformat(),
            window_seconds=window_seconds,
            phase=phase,
            interview_rollout_percent=interview_rollout_percent,
            review_rollout_percent=review_rollout_percent,
            oldest_outbox_age_seconds=oldest,
            oldest_unfinished_outbox_age_seconds=oldest,
            checkpoint_row_count=checkpoint_count,
            privacy_audit="FAIL",
            recommendation="HOLD",
            reasons=["not_evaluated"],
            **values,
        )

    def _business_counts(
        self,
        cursor,
        sql,
        names,
        *,
        observed_since: datetime,
        grace_seconds: int,
    ) -> dict[str, int]:
        values = {
            "interview_assigned_count": self._scalar(
                cursor,
                sql.SQL(
                    "SELECT COUNT(*) FROM {sessions} "
                    "WHERE workflow_engine = 'langgraph-v1' "
                    "AND started_at >= %s"
                ).format(**names),
                (observed_since,),
            ),
            "interview_active_count": self._scalar(
                cursor,
                sql.SQL(
                    "SELECT COUNT(*) FROM {sessions} "
                    "WHERE workflow_engine = 'langgraph-v1' "
                    "AND status = 'active'"
                ).format(**names),
            ),
            "interview_retrying_count": self._scalar(
                cursor,
                sql.SQL(
                    "SELECT COUNT(DISTINCT generations.generation_id) "
                    "FROM {generations} AS generations "
                    "JOIN {attempts} AS attempts "
                    "ON attempts.generation_id = generations.generation_id "
                    "WHERE generations.status = 'running' "
                    "AND attempts.status = 'failed'"
                ).format(**names),
            ),
            "interview_terminal_count": self._scalar(
                cursor,
                sql.SQL(
                    "SELECT COUNT(*) FROM {sessions} "
                    "WHERE workflow_engine = 'langgraph-v1' "
                    "AND status = 'finished' AND finished_at >= %s"
                ).format(**names),
                (observed_since,),
            ),
            "review_assigned_count": self._scalar(
                cursor,
                sql.SQL(
                    "SELECT COUNT(*) FROM {jobs} "
                    "WHERE review_engine = 'langgraph-review-v1' "
                    "AND queued_at >= %s"
                ).format(**names),
                (observed_since,),
            ),
            "review_active_count": self._scalar(
                cursor,
                sql.SQL(
                    "SELECT COUNT(*) FROM {jobs} "
                    "WHERE review_engine = 'langgraph-review-v1' "
                    "AND status IN ('queued', 'running', 'retrying')"
                ).format(**names),
            ),
            "review_retrying_count": self._scalar(
                cursor,
                sql.SQL(
                    "SELECT COUNT(*) FROM {jobs} "
                    "WHERE review_engine = 'langgraph-review-v1' "
                    "AND status = 'retrying'"
                ).format(**names),
            ),
            "review_terminal_count": self._scalar(
                cursor,
                sql.SQL(
                    "SELECT COUNT(*) FROM {jobs} "
                    "WHERE review_engine = 'langgraph-review-v1' "
                    "AND status IN ('completed', 'failed') "
                    "AND finished_at >= %s"
                ).format(**names),
                (observed_since,),
            ),
            "review_failed_count": self._scalar(
                cursor,
                sql.SQL(
                    "SELECT COUNT(*) FROM {jobs} "
                    "WHERE review_engine = 'langgraph-review-v1' "
                    "AND status = 'failed' AND finished_at >= %s"
                ).format(**names),
                (observed_since,),
            ),
            "outbox_pending_count": self._outbox_status_count(
                cursor, sql, names, "pending"
            ),
            "outbox_retrying_count": self._outbox_status_count(
                cursor, sql, names, "retrying"
            ),
            "outbox_running_count": self._outbox_status_count(
                cursor, sql, names, "running"
            ),
            "stale_interview_count": self._scalar(
                cursor,
                sql.SQL(
                    "SELECT COUNT(*) FROM {sessions} "
                    "WHERE workflow_engine = 'langgraph-v1' "
                    "AND status = 'active' AND updated_at < %s"
                ).format(**names),
                (observed_since,),
            ),
            "stale_review_count": self._scalar(
                cursor,
                sql.SQL(
                    "SELECT COUNT(*) FROM {runs} "
                    "WHERE status IN ('pending', 'running', 'waiting') "
                    "AND updated_at < %s"
                ).format(**names),
                (observed_since,),
            ),
            "command_conflict_count": self._scalar(
                cursor,
                sql.SQL(
                    "SELECT COUNT(*) FROM {commands} "
                    "WHERE status = 'conflict' AND completed_at >= %s"
                ).format(**names),
                (observed_since,),
            ),
            "generation_chunk_row_count": self._scalar(
                cursor,
                sql.SQL("SELECT COUNT(*) FROM {chunks}").format(**names),
            ),
            "review_artifact_row_count": self._scalar(
                cursor,
                sql.SQL("SELECT COUNT(*) FROM {artifacts}").format(**names),
            ),
            "review_effect_row_count": self._scalar(
                cursor,
                sql.SQL("SELECT COUNT(*) FROM {effects}").format(**names),
            ),
            "expired_running_outbox_lease_count": self._expired_count(
                cursor,
                sql,
                names["outbox"],
                "lease_expires_at",
                grace_seconds,
            ),
            "expired_generation_lease_count": self._expired_count(
                cursor,
                sql,
                names["attempts"],
                "lease_expires_at",
                grace_seconds,
            ),
            "expired_report_lease_count": self._expired_count(
                cursor,
                sql,
                names["jobs"],
                "lease_expires_at",
                grace_seconds,
            ),
            "running_review_effect_count": self._scalar(
                cursor,
                sql.SQL(
                    "SELECT COUNT(*) FROM {effects} WHERE status = 'running'"
                ).format(**names),
            ),
            "expired_review_effect_claim_count": self._expired_count(
                cursor,
                sql,
                names["effects"],
                "claim_expires_at",
                grace_seconds,
            ),
        }
        return values

    def _signal_counts(
        self,
        cursor,
        sql,
        names,
        *,
        observed_since: datetime,
    ) -> dict[str, int]:
        result = {
            field_name: 0 for field_name in self._SIGNAL_FIELDS.values()
        }
        cursor.execute(
            "SELECT to_regclass(%s)",
            (f"{self.table_prefix}_runtime_signal_buckets",),
        )
        if cursor.fetchone()[0] is None:
            return result
        cursor.execute(
            sql.SQL(
                "SELECT signal_code, SUM(signal_count) FROM {signals} "
                "WHERE bucket_start >= %s GROUP BY signal_code"
            ).format(**names),
            (observed_since,),
        )
        for code, count in cursor.fetchall():
            field_name = self._SIGNAL_FIELDS.get(str(code))
            if field_name is not None:
                result[field_name] = int(count)
        return result

    @staticmethod
    def _outbox_status_count(cursor, sql, names, status: str) -> int:
        return PostgresLangGraphCanaryStatusService._scalar(
            cursor,
            sql.SQL(
                "SELECT COUNT(*) FROM {outbox} WHERE status = %s"
            ).format(**names),
            (status,),
        )

    @staticmethod
    def _expired_count(
        cursor,
        sql,
        table,
        lease_column: str,
        grace_seconds: int,
    ) -> int:
        return PostgresLangGraphCanaryStatusService._scalar(
            cursor,
            sql.SQL(
                "SELECT COUNT(*) FROM {table} WHERE status = 'running' "
                "AND {lease_column} <= "
                "NOW() - (%s * INTERVAL '1 second')"
            ).format(
                table=table,
                lease_column=sql.Identifier(lease_column),
            ),
            (grace_seconds,),
        )

    @staticmethod
    def _oldest_unfinished_outbox_age(cursor, sql, names) -> float | None:
        cursor.execute(
            sql.SQL(
                "SELECT EXTRACT(EPOCH FROM (NOW() - MIN(created_at))) "
                "FROM {outbox} "
                "WHERE status IN ('pending', 'retrying', 'running')"
            ).format(**names)
        )
        oldest = cursor.fetchone()[0]
        return max(0.0, float(oldest)) if oldest is not None else None

    @staticmethod
    def _checkpoint_row_count(cursor) -> int:
        cursor.execute("SELECT to_regclass('checkpoints')")
        if cursor.fetchone()[0] is None:
            return 0
        cursor.execute("SELECT COUNT(*) FROM checkpoints")
        return int(cursor.fetchone()[0])

    @staticmethod
    def _phase_for_pair(interview: int, review: int) -> CanaryPhase:
        try:
            return {
                (0, 0): "baseline",
                (1, 0): "interview",
                (0, 1): "review",
                (1, 1): "joint",
            }[(interview, review)]
        except KeyError as exc:
            raise ValueError(
                "initial fencing canary requires rollout pair "
                "0/0, 1/0, 0/1, or 1/1"
            ) from exc

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
