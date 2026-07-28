from datetime import datetime, timedelta, timezone

import pytest

from app.services.langgraph_canary_status import (
    PostgresLangGraphCanaryStatusService,
    evaluate_canary,
)
from scripts.audit_agent_runtime import audit_runtime_control_payloads
from tests.test_runtime_signal_metrics_postgres import (
    _drop_prefix,
    _initialize_canary_tables,
    _short_prefix,
)


pytestmark = pytest.mark.langgraph_fencing_canary


def _snapshot(dsn: str, prefix: str):
    snapshot = PostgresLangGraphCanaryStatusService(
        dsn=dsn,
        table_prefix=prefix,
    ).snapshot(
        observed_since=datetime.now(timezone.utc) - timedelta(minutes=5),
        phase="baseline",
        interview_rollout_percent=0,
        review_rollout_percent=0,
        lease_expiry_grace_seconds=30,
    )
    privacy = audit_runtime_control_payloads(
        [snapshot.model_dump(mode="json")]
    )
    return snapshot.model_copy(update={"privacy_audit": privacy["status"]})


def test_transient_ownership_incident_survives_mutable_control_recovery(
    postgres_dsn,
):
    prefix = _short_prefix()
    try:
        _, _, signals = _initialize_canary_tables(postgres_dsn, prefix)
        signals.increment(
            workflow_type="review",
            signal_code="report_lease_lost",
        )

        snapshot = _snapshot(postgres_dsn, prefix)
        result = evaluate_canary(snapshot)

        assert snapshot.report_lease_lost_count == 1
        assert result.recommendation == "HOLD"
        assert result.reasons == ["ownership_anomaly"]
    finally:
        _drop_prefix(postgres_dsn, prefix)


@pytest.mark.parametrize(
    ("workflow_type", "signal_code", "field_name", "reason"),
    [
        (
            "interview",
            "projection_conflict",
            "projection_divergence_count",
            "projection_conflict",
        ),
        (
            "review",
            "review_effect_conflict",
            "review_effect_conflict_count",
            "review_effect_conflict",
        ),
        (
            "review",
            "report_commit_conflict",
            "report_commit_conflict_count",
            "report_commit_conflict",
        ),
    ],
)
def test_persistent_correctness_incident_forces_rollback(
    postgres_dsn,
    workflow_type,
    signal_code,
    field_name,
    reason,
):
    prefix = _short_prefix()
    try:
        _, _, signals = _initialize_canary_tables(postgres_dsn, prefix)
        signals.increment(
            workflow_type=workflow_type,
            signal_code=signal_code,
        )

        snapshot = _snapshot(postgres_dsn, prefix)
        result = evaluate_canary(snapshot)

        assert getattr(snapshot, field_name) == 1
        assert result.recommendation == "ROLL_BACK"
        assert result.reasons == [reason]
    finally:
        _drop_prefix(postgres_dsn, prefix)


def test_busy_incident_holds_without_becoming_correctness_rollback(
    postgres_dsn,
):
    prefix = _short_prefix()
    try:
        _, _, signals = _initialize_canary_tables(postgres_dsn, prefix)
        signals.increment(
            workflow_type="interview",
            signal_code="workflow_thread_busy",
        )

        result = evaluate_canary(_snapshot(postgres_dsn, prefix))

        assert result.recommendation == "HOLD"
        assert result.reasons == ["workflow_thread_busy"]
    finally:
        _drop_prefix(postgres_dsn, prefix)
