import pytest
import json
from pydantic import ValidationError

from app.services.followup_diagnostics import (
    FollowupDiagnosticInput,
    FollowupDiagnosticRejected,
    FollowupPolicySnapshot,
    diagnose_followup,
    stable_followup_fingerprint,
)


def request(**updates):
    payload = {
        "session_id": "s1",
        "question_id": "q1",
        "question_text": "如何保证消息处理的幂等性？",
        "focus": "幂等、失败恢复与监控",
        "candidate_answers": ["使用业务幂等键并持久化处理结果。"],
        "asked_followups": [],
        "followup_count": 0,
        "closed_gap_ids": [],
        "public_knowledge_summary": "可讨论唯一键和补偿，但不能把知识当成候选人陈述。",
        "policy": {
            "policy_version": "adaptive_v1",
            "max_followups": 2,
            "max_context_chars": 1200,
        },
    }
    payload.update(updates)
    return payload


@pytest.mark.parametrize(
    ("updates", "reason"),
    [
        ({"session_status": "finished"}, "session_finished"),
        ({"command_expired": True}, "stale_command"),
    ],
)
def test_finished_or_stale_commands_are_rejected_before_diagnosis(updates, reason):
    with pytest.raises(FollowupDiagnosticRejected) as exc:
        diagnose_followup(request(**updates))
    assert exc.value.reason_code == reason


@pytest.mark.parametrize(
    ("updates", "reason"),
    [
        ({"followup_count": 2, "asked_followups": ["a", "b"]}, "followup_limit_reached"),
        ({"question_closed": True}, "question_closed"),
        ({"skip_command": True}, "skip_command"),
    ],
)
def test_hard_next_rules_run_without_provider(updates, reason):
    result = diagnose_followup(request(**updates))

    assert result.provider_allowed is False
    assert result.deterministic_decision.action == "next_question"
    assert result.deterministic_decision.reason_code == reason
    assert result.provider_context == {}
    assert result.context_truncated is False


def test_hard_rule_does_not_construct_an_oversized_provider_context():
    result = diagnose_followup(
        request(
            followup_count=2,
            asked_followups=["追问" * 1_000, "再次追问" * 600],
            candidate_answers=["回答" * 1_000] * 3,
            public_knowledge_summary="公开知识" * 1_000,
            policy={
                "policy_version": "adaptive_v1",
                "max_followups": 2,
                "max_context_chars": 512,
            },
        )
    )

    assert result.deterministic_decision.reason_code == "followup_limit_reached"
    assert result.provider_allowed is False
    assert result.provider_context == {}


def test_empty_answer_allows_one_clarification_then_forces_next():
    first = diagnose_followup(request(candidate_answers=["不知道"]))
    second = diagnose_followup(
        request(
            candidate_answers=["不知道", "还是不知道"],
            asked_followups=["可以先说明一个关键点吗？"],
            followup_count=1,
        )
    )

    assert first.signals == ["empty"]
    assert first.deterministic_decision.action == "follow_up"
    assert first.deterministic_decision.gap_type == "clarification"
    assert second.deterministic_decision.action == "next_question"


def test_regular_answer_produces_bounded_provider_context_and_stable_fingerprints():
    payload = request(
        candidate_answers=["先写入去重表。" + "补充细节" * 400],
        closed_gap_ids=["gap-timeout"],
        public_knowledge_summary="公开知识" * 500,
    )
    payload["policy"] = {
        "policy_version": "adaptive_v1",
        "max_followups": 2,
        "max_context_chars": 512,
    }
    result = diagnose_followup(payload)

    assert result.provider_allowed is True
    assert result.deterministic_decision is None
    assert result.context_truncated is True
    assert len(result.input_sha256) == 64
    assert len(result.forbidden_gap_fingerprints[0]) == 64
    assert "closed_gap_ids" not in result.provider_context
    assert len(
        json.dumps(
            result.provider_context,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    ) <= 512
    assert result.forbidden_question_fingerprints == diagnose_followup(payload).forbidden_question_fingerprints


def test_existing_gap_fingerprint_is_not_hashed_twice():
    fingerprint = stable_followup_fingerprint("missing recovery")
    result = diagnose_followup(
        request(closed_gap_ids=[fingerprint], open_gap_id=fingerprint)
    )

    assert result.forbidden_gap_fingerprints == [fingerprint]
    assert result.provider_context["open_gap_fingerprint"] == fingerprint


def test_diagnostics_input_cannot_accept_scores_or_report_fields():
    with pytest.raises(ValidationError, match="Extra inputs"):
        FollowupDiagnosticInput.model_validate(request(overall_score=80))


def test_policy_never_allows_more_than_two_followups():
    with pytest.raises(ValidationError):
        FollowupPolicySnapshot(policy_version="adaptive_v1", max_followups=3)


def test_fixed_policy_preserves_exactly_one_deterministic_followup():
    first = diagnose_followup(
        request(policy={"policy_version": "fixed_v1", "max_followups": 1})
    )
    second = diagnose_followup(
        request(
            policy={"policy_version": "fixed_v1", "max_followups": 1},
            candidate_answers=["first answer", "second answer"],
            asked_followups=["one follow-up"],
            followup_count=1,
        )
    )

    assert first.provider_allowed is False
    assert first.deterministic_decision.action == "follow_up"
    assert first.deterministic_decision.reason_code == "fixed_policy_followup"
    assert second.provider_allowed is False
    assert second.deterministic_decision.action == "next_question"
    assert second.deterministic_decision.reason_code == "followup_limit_reached"
