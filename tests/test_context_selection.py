from __future__ import annotations

from app.services.context_selection import (
    OMISSION_MARKER,
    group_conversation_units,
    select_interview_messages,
    truncate_text_to_tokens,
)
from app.services.token_estimation import ConservativeUtf8TokenEstimator


def test_legacy_messages_use_conservative_grouping():
    units = group_conversation_units(
        [
            {"role": "interviewer", "content": "q"},
            {"role": "candidate", "content": "a"},
            {"role": "candidate", "content": "retry"},
        ]
    )
    assert [unit.grouping_path for unit in units] == [
        "legacy_role_pair",
        "legacy_unscoped",
    ]


def test_explicit_question_id_is_authoritative():
    units = group_conversation_units(
        [
            {"role": "interviewer", "content": "q1", "question_id": "q1"},
            {"role": "candidate", "content": "a1", "question_id": "q1"},
            {"role": "interviewer", "content": "q2", "question_id": "q2"},
        ]
    )
    assert [unit.question_id for unit in units] == ["q1", "q2"]


def test_truncation_keeps_head_tail_and_marker():
    estimator = ConservativeUtf8TokenEstimator()
    text = "A" * 100 + "Z" * 100
    result, truncated = truncate_text_to_tokens(
        text,
        token_budget=80,
        estimator=estimator,
        model="unknown",
    )
    assert truncated is True
    assert result.startswith("A")
    assert result.endswith("Z")
    assert OMISSION_MARKER in result
    assert estimator.estimate_text(result, model="unknown") <= 80


def test_interview_selection_retains_latest_current_answer_without_mutation():
    estimator = ConservativeUtf8TokenEstimator()
    messages = [
        {"role": "interviewer", "content": "old q", "question_id": "q1"},
        {"role": "candidate", "content": "old a", "question_id": "q1"},
        {"role": "interviewer", "content": "current q", "question_id": "q2"},
        {
            "role": "candidate",
            "content": "current answer " * 40,
            "question_id": "q2",
        },
    ]
    original = [dict(message) for message in messages]
    selected, stats = select_interview_messages(
        messages,
        current_question_id="q2",
        token_budget=160,
        max_single_message_tokens=120,
        estimator=estimator,
        model="unknown",
    )
    assert any(message["role"] == "candidate" for message in selected)
    assert messages == original
    assert stats.source_message_count == 4
    assert stats.selected_message_count >= 1
