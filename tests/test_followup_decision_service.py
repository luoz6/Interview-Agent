from app.services.decision_store import DecisionContract, InMemoryDecisionStore
from app.services.followup_decision_service import FollowupDecisionExecutionService


def request(**updates):
    payload = {
        "session_id": "s1",
        "question_id": "q1",
        "question_text": "How do you make message delivery idempotent?",
        "focus": "idempotency and recovery",
        "candidate_answers": ["I persist an idempotency key before processing."],
        "asked_followups": [],
        "followup_count": 0,
        "closed_gap_ids": [],
        "public_knowledge_summary": "",
        "policy": {"policy_version": "adaptive_v1", "max_followups": 2},
    }
    payload.update(updates)
    return payload


def provider_decision(**updates):
    payload = {
        "action": "follow_up",
        "answer_state": "partial",
        "gap_type": "failure_mode",
        "gap_summary": "The answer does not explain recovery after a failed write.",
        "reason_code": "missing_failure_mode",
        "decision_confidence": "high",
        "closed_gap_ids": [],
        "policy_version": "adaptive_v1",
    }
    payload.update(updates)
    return payload


def test_completed_decision_replays_without_second_provider_call():
    calls = []
    service = FollowupDecisionExecutionService(
        store=InMemoryDecisionStore(),
        provider=lambda context: calls.append(context) or provider_decision(),
    )

    first = service.execute(request(), source_command_id="cmd-1", worker_id="w1")
    replay = service.execute(request(), source_command_id="cmd-1", worker_id="w2")

    assert first.status == replay.status == "completed"
    assert first.decision_id == replay.decision_id
    assert first.provider_invocations == 1
    assert replay.provider_invocations == 0
    assert replay.replayed is True
    assert len(calls) == 1


def test_deterministic_limit_decision_makes_zero_provider_calls():
    service = FollowupDecisionExecutionService(
        store=InMemoryDecisionStore(),
        provider=lambda context: (_ for _ in ()).throw(
            AssertionError("provider must not run after the follow-up limit")
        ),
    )

    result = service.execute(
        request(
            candidate_answers=["one", "two", "three"],
            asked_followups=["f1", "f2"],
            followup_count=2,
        ),
        source_command_id="cmd-limit",
        worker_id="w1",
    )

    assert result.decision.action == "next_question"
    assert result.decision.reason_code == "followup_limit_reached"
    assert result.provider_invocations == 0


def test_valid_lease_owned_by_other_worker_returns_accepted():
    store = InMemoryDecisionStore()
    from app.services.followup_diagnostics import diagnose_followup

    diagnostics = diagnose_followup(request())
    record = store.prepare(
        session_id="s1",
        source_command_id="cmd-busy",
        input_sha256=diagnostics.input_sha256,
    )
    store.claim(record.decision_id, worker_id="owner")
    service = FollowupDecisionExecutionService(
        store=store,
        provider=lambda context: (_ for _ in ()).throw(
            AssertionError("busy decisions do not invoke the provider")
        ),
    )

    result = service.execute(
        request(), source_command_id="cmd-busy", worker_id="other"
    )

    assert result.status == "accepted"
    assert result.decision is None
    assert result.provider_invocations == 0


def test_invalid_output_retries_once_then_persists_safe_next_fallback():
    calls = []
    store = InMemoryDecisionStore(max_attempts=2)
    service = FollowupDecisionExecutionService(
        store=store,
        provider=lambda context: calls.append(context)
        or {**provider_decision(), "score": 100},
    )

    result = service.execute(
        request(), source_command_id="cmd-invalid", worker_id="w1"
    )

    assert result.status == "completed"
    assert result.decision.action == "next_question"
    assert result.decision.reason_code == "provider_invalid_output"
    assert result.provider_invocations == 2
    assert [item.status for item in store.list_attempts(result.decision_id)] == [
        "failed",
        "completed",
    ]
    assert len(calls) == 2


def test_low_confidence_followup_is_conservatively_persisted_as_next():
    service = FollowupDecisionExecutionService(
        store=InMemoryDecisionStore(),
        provider=lambda context: provider_decision(decision_confidence="low"),
    )

    result = service.execute(
        request(), source_command_id="cmd-low-confidence", worker_id="w1"
    )

    assert result.decision.action == "next_question"
    assert result.decision.reason_code == "low_confidence"


def test_provider_usage_is_returned_without_entering_decision_contract():
    service = FollowupDecisionExecutionService(
        store=InMemoryDecisionStore(),
        provider=lambda context: {
            "decision": provider_decision(),
            "input_tokens": 123,
            "output_tokens": 45,
        },
    )

    result = service.execute(
        request(), source_command_id="cmd-usage", worker_id="w1"
    )

    assert result.input_tokens == 123
    assert result.output_tokens == 45
    assert "input_tokens" not in result.decision.model_dump(mode="json")
    attempt = service.store.list_attempts(result.decision_id)[0]
    assert attempt.provider_invocations == 1
    assert attempt.input_tokens == 123
    assert attempt.output_tokens == 45
    assert attempt.duration_ms is not None
