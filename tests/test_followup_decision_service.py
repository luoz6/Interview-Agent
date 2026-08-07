from app.services.decision_store import DecisionContract, InMemoryDecisionStore
from app.services.followup_decision_service import FollowupDecisionExecutionService
from app.services.followup_prompts import (
    FOLLOWUP_DECISION_PROMPT_SHA256,
    FOLLOWUP_DECISION_PROMPT_VERSION,
)
from app.services.followup_diagnostics import stable_followup_fingerprint


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


def test_invalid_structured_attempts_preserve_safe_usage_and_hashed_trace():
    calls = []
    store = InMemoryDecisionStore(max_attempts=2)

    def provider(context):
        calls.append(context)
        error = ValueError("private invalid Provider output")
        error.input_tokens = 100 + len(calls)
        error.output_tokens = 5
        error.cached_input_tokens = 20
        error.provider_response_id = f"private-response-{len(calls)}"
        raise error

    service = FollowupDecisionExecutionService(store=store, provider=provider)
    result = service.execute(
        request(), source_command_id="cmd-invalid-metered", worker_id="w1"
    )

    attempts = store.list_attempts(result.decision_id)
    assert [item.status for item in attempts] == ["failed", "completed"]
    assert [item.input_tokens for item in attempts] == [101, 102]
    assert [item.output_tokens for item in attempts] == [5, 5]
    assert [item.cached_input_tokens for item in attempts] == [20, 20]
    assert all(item.provider_response_id_sha256 for item in attempts)
    assert "private-response" not in "".join(
        item.model_dump_json() for item in attempts
    )


def test_invalid_cached_usage_fails_safely_without_stranding_running_attempt():
    store = InMemoryDecisionStore(max_attempts=1)
    service = FollowupDecisionExecutionService(
        store=store,
        provider=lambda context: {
            "decision": provider_decision(),
            "input_tokens": 10,
            "output_tokens": 2,
            "cached_input_tokens": 11,
            "provider_response_id": "private-invalid-cache-response",
        },
    )

    result = service.execute(
        request(), source_command_id="cmd-invalid-cache", worker_id="w1"
    )
    attempt = store.list_attempts(result.decision_id)[0]

    assert result.status == "completed"
    assert result.decision.reason_code == "provider_invalid_output"
    assert attempt.status == "completed"
    assert attempt.provider_invocations == 1
    assert attempt.input_tokens == 10
    assert attempt.output_tokens == 2
    assert attempt.cached_input_tokens is None
    assert attempt.provider_response_id_sha256 is not None
    assert "private-invalid-cache-response" not in attempt.model_dump_json()


def test_repeated_provider_failure_for_off_topic_answer_forces_safe_next():
    calls = []

    def failed_provider(context):
        calls.append(context)
        raise RuntimeError("provider unavailable")

    service = FollowupDecisionExecutionService(
        store=InMemoryDecisionStore(max_attempts=2),
        provider=failed_provider,
    )

    result = service.execute(
        request(
            candidate_answers=["This is off topic and does not answer the question."],
        ),
        source_command_id="cmd-provider-failed-off-topic",
        worker_id="w1",
    )

    assert result.decision.action == "next_question"
    assert result.decision.reason_code == "provider_failed"
    assert result.provider_invocations == 2
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
            "cached_input_tokens": 67,
            "provider_response_id": "secret-provider-response-id",
        },
    )

    result = service.execute(
        request(), source_command_id="cmd-usage", worker_id="w1"
    )

    assert result.input_tokens == 123
    assert result.output_tokens == 45
    assert result.cached_input_tokens == 67
    assert result.provider_response_id_sha256 is not None
    assert result.provider_response_id_sha256 != "secret-provider-response-id"
    assert "input_tokens" not in result.decision.model_dump(mode="json")
    attempt = service.store.list_attempts(result.decision_id)[0]
    assert attempt.provider_invocations == 1
    assert attempt.input_tokens == 123
    assert attempt.output_tokens == 45
    assert attempt.cached_input_tokens == 67
    assert attempt.provider_response_id_sha256 == result.provider_response_id_sha256
    assert "secret-provider-response-id" not in attempt.model_dump_json()
    assert attempt.duration_ms is not None
    record = service.store.get(result.decision_id)
    assert record.decision_prompt_version == FOLLOWUP_DECISION_PROMPT_VERSION
    assert record.decision_prompt_sha256 == FOLLOWUP_DECISION_PROMPT_SHA256


def test_repeated_gap_is_closed_and_safely_terminates_question():
    previous_gap = stable_followup_fingerprint(
        "The answer does not explain recovery after a failed write."
    )
    service = FollowupDecisionExecutionService(
        store=InMemoryDecisionStore(),
        provider=lambda context: provider_decision(),
    )

    result = service.execute(
        request(
            candidate_answers=["first", "second"],
            asked_followups=["How do you recover a failed write?"],
            followup_count=1,
            open_gap_id=previous_gap,
        ),
        source_command_id="cmd-duplicate-gap",
        worker_id="w1",
    )

    assert result.decision.action == "next_question"
    assert result.decision.reason_code == "duplicate_gap"
    assert result.decision.closed_gap_ids == [previous_gap]


def test_distinct_second_gap_closes_previous_server_owned_gap():
    previous_gap = stable_followup_fingerprint("Missing write recovery.")
    service = FollowupDecisionExecutionService(
        store=InMemoryDecisionStore(),
        provider=lambda context: provider_decision(
            gap_type="tradeoff",
            gap_summary="The latency and consistency tradeoff is still missing.",
            reason_code="missing_tradeoff",
        ),
    )

    result = service.execute(
        request(
            candidate_answers=["first", "second"],
            asked_followups=["How do you recover the write?"],
            followup_count=1,
            open_gap_id=previous_gap,
        ),
        source_command_id="cmd-distinct-gap",
        worker_id="w1",
    )

    assert result.decision.action == "follow_up"
    assert result.decision.closed_gap_ids == [previous_gap]


def test_provider_cannot_invent_server_owned_closed_gap_ids():
    service = FollowupDecisionExecutionService(
        store=InMemoryDecisionStore(max_attempts=1),
        provider=lambda context: provider_decision(
            closed_gap_ids=[stable_followup_fingerprint("invented")]
        ),
    )

    result = service.execute(
        request(), source_command_id="cmd-invented-gap", worker_id="w1"
    )

    assert result.decision.action == "next_question"
    assert result.decision.reason_code == "provider_invalid_output"
