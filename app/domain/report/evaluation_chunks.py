from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel

from app.domain.interview.prep import InterviewQuestion
from app.domain.interview.published_question import (
    published_question_ids,
    published_question_text,
    question_id,
    question_kind,
)


class EvaluationChunk(BaseModel):
    question_id: str
    question_text: str
    question_kind: str
    focus: str
    answer_state: str
    messages: list[dict[str, str]]


def build_evaluation_chunks(
    state: Mapping[str, Any],
) -> list[EvaluationChunk]:
    questions = state["plan"].questions
    if getattr(state["plan"], "schema_version", None) == "interview-plan-v3":
        published_ids = published_question_ids(state)
        questions = [
            question
            for question in questions
            if question_id(question) in published_ids
        ]
    return [
        EvaluationChunk(
            question_id=question_id(question),
            question_text=published_question_text(state, question),
            question_kind=question_kind(question),
            focus=question.focus,
            answer_state=_answer_state_for_question(state, question),
            messages=_messages_for_question(state, question),
        )
        for question in questions
    ]


def _answer_state_for_question(
    state: Mapping[str, Any],
    question: InterviewQuestion,
) -> str:
    target_id = question_id(question)
    if target_id in state.get("skipped_question_ids", []):
        return "skipped"
    has_answer = any(
        message["role"] == "candidate"
        and message["question_id"] == target_id
        and message["content"].strip()
        for message in state["messages"]
    )
    if has_answer:
        return "answered"
    return "unanswered"


def _messages_for_question(
    state: Mapping[str, Any],
    question: InterviewQuestion,
) -> list[dict[str, str]]:
    target_id = question_id(question)
    return [
        {"role": message["role"], "content": message["content"]}
        for message in state["messages"]
        if message["question_id"] == target_id
    ]


__all__ = ["EvaluationChunk", "build_evaluation_chunks"]
