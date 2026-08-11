"""Privacy and schema contracts for runtime signal metrics."""

from datetime import datetime, timezone

import pytest

from app.services.runtime_signal_metrics import (
    CANARY_SIGNAL_CODES,
    WORKFLOW_SIGNAL_TYPES,
    NoopRuntimeSignalStore,
    validate_canary_signal_code,
    validate_observed_since,
    validate_workflow_signal_type,
)


def test_signal_contract_uses_closed_privacy_safe_allowlists():
    assert WORKFLOW_SIGNAL_TYPES == {"interview", "review", "shared"}
    assert CANARY_SIGNAL_CODES == {
        "workflow_thread_busy",
        "workflow_thread_lock_lost",
        "generation_lease_lost",
        "fenced_write_rejected",
        "projection_conflict",
        "report_lease_lost",
        "review_effect_busy",
        "review_effect_conflict",
        "report_commit_conflict",
        "canary_signal_write_failed",
    }


@pytest.mark.parametrize(
    "value",
    ["", "candidate answer", "provider payload", "session-123", "custom"],
)
def test_free_form_or_identity_like_signal_codes_are_rejected(value):
    with pytest.raises(ValueError, match="unsupported canary signal code"):
        validate_canary_signal_code(value)


def test_unknown_workflow_type_is_rejected():
    with pytest.raises(ValueError, match="unsupported workflow signal type"):
        validate_workflow_signal_type("session-123")


def test_observed_since_must_be_timezone_aware():
    with pytest.raises(ValueError, match="timezone-aware"):
        validate_observed_since(datetime(2026, 7, 25))


def test_noop_store_preserves_validation_without_recording_identity():
    store = NoopRuntimeSignalStore()

    store.increment(
        workflow_type="interview", signal_code="workflow_thread_busy"
    )
    assert store.sum_since(datetime.now(timezone.utc)) == {}
    assert store.cleanup_older_than(hours=168) == 0

    with pytest.raises(ValueError):
        store.increment(workflow_type="interview", signal_code="private text")
