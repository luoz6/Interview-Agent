from datetime import datetime, timedelta, timezone

import pytest

from app.services.decision_store import (
    DecisionContract,
    DecisionStoreConflict,
    InMemoryDecisionStore,
)


def decision(action="follow_up"):
    return DecisionContract(
        action=action,
        answer_state="partial",
        gap_type="evidence" if action == "follow_up" else "none",
        gap_summary="Need one concrete failure mode." if action == "follow_up" else "",
        reason_code="missing_evidence" if action == "follow_up" else "answer_complete",
        decision_confidence="medium",
        closed_gap_ids=[],
        policy_version="adaptive_v1",
    )


def test_decision_prepare_is_idempotent_and_completed_replay_is_stable():
    store = InMemoryDecisionStore()
    first = store.prepare(session_id="s1", source_command_id="cmd-1", input_sha256="a" * 64)
    replay = store.prepare(session_id="s1", source_command_id="cmd-1", input_sha256="a" * 64)
    assert replay.decision_id == first.decision_id
    attempt = store.claim(first.decision_id, worker_id="w1")
    completed = store.complete(
        attempt.attempt_id,
        worker_id="w1",
        lease_token=attempt.lease_token,
        decision=decision(),
    )
    assert completed.status == "completed"
    assert store.get(first.decision_id).decision_sha256 == completed.decision_sha256


def test_decision_prompt_lineage_replay_is_compatible_with_legacy_nulls():
    store = InMemoryDecisionStore()
    legacy = store.prepare(
        session_id="s1",
        source_command_id="cmd-legacy",
        input_sha256="b" * 64,
    )

    replay = store.prepare(
        session_id="s1",
        source_command_id="cmd-legacy",
        input_sha256="b" * 64,
        decision_prompt_version="followup-decision-v1",
        decision_prompt_sha256="c" * 64,
    )

    assert replay.decision_id == legacy.decision_id
    assert replay.decision_prompt_version is None
    assert replay.decision_prompt_sha256 is None


def test_decision_prompt_lineage_replay_rejects_non_null_drift():
    store = InMemoryDecisionStore()
    first = store.prepare(
        session_id="s1",
        source_command_id="cmd-prompt",
        input_sha256="d" * 64,
        decision_prompt_version="followup-decision-v1",
        decision_prompt_sha256="e" * 64,
    )
    replay = store.prepare(
        session_id="s1",
        source_command_id="cmd-prompt",
        input_sha256="d" * 64,
        decision_prompt_version="followup-decision-v1",
        decision_prompt_sha256="e" * 64,
    )

    assert replay.decision_id == first.decision_id
    with pytest.raises(DecisionStoreConflict, match="prompt conflicts"):
        store.prepare(
            session_id="s1",
            source_command_id="cmd-prompt",
            input_sha256="d" * 64,
            decision_prompt_version="followup-decision-v2",
            decision_prompt_sha256="f" * 64,
        )


def test_decision_fencing_rejects_late_worker_and_failure_creates_bounded_retry():
    store = InMemoryDecisionStore(max_attempts=2)
    record = store.prepare(session_id="s1", source_command_id="cmd-1", input_sha256="a" * 64)
    first = store.claim(record.decision_id, worker_id="w1")
    store.fail(first.attempt_id, worker_id="w1", lease_token=first.lease_token, error_code="provider_timeout")
    second = store.claim(record.decision_id, worker_id="w2")
    with pytest.raises(DecisionStoreConflict):
        store.complete(first.attempt_id, worker_id="w1", lease_token=first.lease_token, decision=decision())
    store.fail(second.attempt_id, worker_id="w2", lease_token=second.lease_token, error_code="invalid_output")
    assert store.get(record.decision_id).status == "failed"
    assert len(store.list_attempts(record.decision_id)) == 2
    with pytest.raises(DecisionStoreConflict, match="failed decision"):
        store.claim(record.decision_id, worker_id="w3")


def test_next_question_contract_has_no_gap():
    with pytest.raises(ValueError):
        DecisionContract(
            action="next_question",
            answer_state="complete",
            gap_type="evidence",
            gap_summary="bad",
            reason_code="answer_complete",
            decision_confidence="high",
            policy_version="fixed_v1",
        )
