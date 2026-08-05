import pytest
from pydantic import ValidationError

from app.services.decision_store import DecisionContract


def valid_followup(**updates):
    payload = {
        "action": "follow_up",
        "answer_state": "partial",
        "gap_type": "failure_mode",
        "gap_summary": "没有说明失败后的补偿路径。",
        "reason_code": "missing_failure_mode",
        "decision_confidence": "medium",
        "closed_gap_ids": ["gap-timeout"],
        "policy_version": "adaptive_v1",
    }
    payload.update(updates)
    return payload


def test_decision_contract_accepts_chinese_json_without_question_text():
    decision = DecisionContract.model_validate(valid_followup())

    assert decision.action == "follow_up"
    assert "question" not in decision.model_dump(mode="json")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("score", 80),
        ("follow_up_question", "为什么？"),
        ("reasoning", "hidden chain"),
    ],
)
def test_decision_contract_rejects_provider_extras(field, value):
    with pytest.raises(ValidationError, match="Extra inputs"):
        DecisionContract.model_validate(valid_followup(**{field: value}))


def test_decision_contract_rejects_missing_fields_and_unknown_enums():
    payload = valid_followup()
    payload.pop("reason_code")
    with pytest.raises(ValidationError):
        DecisionContract.model_validate(payload)
    with pytest.raises(ValidationError):
        DecisionContract.model_validate(valid_followup(reason_code="free_text"))


@pytest.mark.parametrize(
    "updates",
    [
        {"gap_summary": "x" * 241},
        {"gap_summary": "line one\nline two"},
        {"gap_summary": "标准答案是先提交数据库再删除缓存"},
        {"closed_gap_ids": ["same", "same"]},
        {"action": "follow_up", "gap_type": "none"},
        {
            "action": "next_question",
            "gap_type": "none",
            "gap_summary": "still open",
            "reason_code": "answer_complete",
        },
    ],
)
def test_decision_contract_enforces_cross_field_and_disclosure_rules(updates):
    with pytest.raises(ValidationError):
        DecisionContract.model_validate(valid_followup(**updates))


def test_next_question_has_no_generation_payload():
    decision = DecisionContract.model_validate(
        valid_followup(
            action="next_question",
            answer_state="complete",
            gap_type="none",
            gap_summary="",
            reason_code="answer_complete",
            decision_confidence="high",
        )
    )

    assert decision.gap_type == "none"
    assert decision.gap_summary == ""
