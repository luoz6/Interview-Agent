import pytest
import json
from pydantic import ValidationError

from app.services.followup_diagnostics import (
    FOLLOWUP_DIAGNOSTICS_VERSION,
    FOLLOWUP_TEXT_MIN_NORMALIZED_CHARS,
    FOLLOWUP_TEXT_SIMILARITY_THRESHOLD,
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


@pytest.mark.parametrize(
    "answers",
    [
        [
            "我会先写入业务幂等键并持久化处理结果。",
            "我会先写入业务幂等键，并持久化处理结果。",
        ],
        [
            "I persist the idempotency key before processing the message.",
            "I persist the idempotency key before processing the message!",
        ],
    ],
)
def test_repeated_answer_is_also_no_new_information(answers):
    result = diagnose_followup(request(candidate_answers=answers))

    assert result.signals == ["repeated_answer", "no_new_information"]


@pytest.mark.parametrize(
    ("question_text", "answer"),
    [
        (
            "如何保证消息处理的幂等性并验证失败恢复？",
            "如何保证消息处理的幂等性，并验证失败恢复？",
        ),
        (
            "How do you guarantee idempotent processing and validate recovery?",
            "How do you guarantee idempotent processing and validate recovery!",
        ),
    ],
)
def test_answer_only_repeating_question_is_no_new_information(
    question_text, answer
):
    result = diagnose_followup(
        request(question_text=question_text, candidate_answers=[answer])
    )

    assert result.signals == [
        "no_new_information",
        "answer_only_repeats_question",
    ]


@pytest.mark.parametrize(
    ("prior_answer", "asked_followup"),
    [
        (
            "我会使用业务幂等键拒绝重复请求。",
            "请说明失败写入后的恢复步骤和验证指标。",
        ),
        (
            "I reject duplicate requests with a business idempotency key.",
            "Please explain recovery steps and validation metrics after a failed write.",
        ),
    ],
)
def test_repeating_only_the_asked_followup_has_no_new_information(
    prior_answer, asked_followup
):
    result = diagnose_followup(
        request(
            candidate_answers=[prior_answer, asked_followup],
            asked_followups=[asked_followup],
            followup_count=1,
        )
    )

    assert result.signals == ["no_new_information"]


@pytest.mark.parametrize(
    ("question_text", "prior_answer", "asked_followup", "latest_answer"),
    [
        (
            "如何保证消息处理的幂等性？",
            "我会使用唯一业务键拒绝重复请求。",
            "失败写入后如何恢复？",
            "我会从持久化日志重放失败写入，并监控补偿结果。",
        ),
        (
            "How do you make message processing idempotent?",
            "I reject duplicate requests with a unique business key.",
            "How do you recover a failed write?",
            "I replay failed writes from the durable log and monitor compensation.",
        ),
    ],
)
def test_new_information_is_not_misclassified_as_any_repeat_signal(
    question_text, prior_answer, asked_followup, latest_answer
):
    result = diagnose_followup(
        request(
            question_text=question_text,
            candidate_answers=[prior_answer, latest_answer],
            asked_followups=[asked_followup],
            followup_count=1,
        )
    )

    assert not {
        "repeated_answer",
        "no_new_information",
        "answer_only_repeats_question",
    }.intersection(result.signals)


@pytest.mark.parametrize("answer", ["是的", "yes"])
def test_short_repeated_answers_are_not_misclassified_as_repeat_signals(answer):
    result = diagnose_followup(request(candidate_answers=[answer, answer]))

    assert result.signals == ["very_short"]


def test_repeat_signal_thresholds_and_minimum_length_boundary_are_frozen():
    assert FOLLOWUP_TEXT_MIN_NORMALIZED_CHARS == 12
    assert FOLLOWUP_TEXT_SIMILARITY_THRESHOLD == 0.9

    below = diagnose_followup(
        request(candidate_answers=["abcdefghijk", "abcdefghijk"])
    )
    at_boundary = diagnose_followup(
        request(candidate_answers=["abcdefghijkl", "abcdefghijkl"])
    )

    assert below.signals == ["very_short"]
    assert at_boundary.signals == ["repeated_answer", "no_new_information"]


def test_repeat_signals_do_not_reopen_a_closed_question_or_gap():
    closed_gap = stable_followup_fingerprint("missing recovery evidence")
    result = diagnose_followup(
        request(
            question_closed=True,
            candidate_answers=[
                "我会通过持久化日志恢复失败写入并验证补偿结果。",
                "我会通过持久化日志恢复失败写入，并验证补偿结果。",
            ],
            closed_gap_ids=[closed_gap],
        )
    )

    assert result.signals == ["repeated_answer", "no_new_information"]
    assert result.provider_allowed is False
    assert result.deterministic_decision.action == "next_question"
    assert result.deterministic_decision.reason_code == "question_closed"
    assert result.deterministic_decision.closed_gap_ids == [closed_gap]


def test_diagnostics_v2_preserves_replay_input_and_provider_context_contract():
    payload = request(
        question_text=(
            "How do you guarantee idempotent processing and validate recovery?"
        ),
        focus="idempotency, failure recovery, and monitoring",
        candidate_answers=[
            "I persist the idempotency key before processing the message.",
            "I persist the idempotency key before processing the message!",
        ],
        asked_followups=["Please add one concrete implementation detail."],
        followup_count=1,
        public_knowledge_summary="",
    )
    result = diagnose_followup(payload)

    assert FOLLOWUP_DIAGNOSTICS_VERSION == "followup-diagnostics-v2"
    assert result.diagnostics_version == FOLLOWUP_DIAGNOSTICS_VERSION
    assert result.input_sha256 == (
        "d0498b9eb3cf8f8cd9f1d276dbe150675587571106e22850dabebc80f8fb3167"
    )
    assert result.provider_allowed is True
    assert result.deterministic_decision is None
    assert result.provider_context == {
        "question_id": "q1",
        "question": (
            "How do you guarantee idempotent processing and validate recovery?"
        ),
        "focus": "idempotency, failure recovery, and monitoring",
        "candidate_answers": payload["candidate_answers"],
        "asked_followups": payload["asked_followups"],
        "followup_count": 1,
        "closed_gap_fingerprints": [],
        "open_gap_fingerprint": None,
        "public_knowledge_summary": "",
        "policy": {
            "policy_version": "adaptive_v1",
            "max_followups": 2,
            "max_context_chars": 1200,
            "empty_clarification_limit": 1,
        },
    }
    assert "signals" not in result.provider_context
    assert "diagnostics_version" not in result.provider_context


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
