"""Compatibility exports for Domain-owned interview state transitions."""

from app.domain.interview.transitions import (
    INTERVIEW_FINISHED_MESSAGE,
    _elapsed_seconds,
    _ensure_state_metadata,
    _has_terminal_message,
    _parse_state_timestamp,
    _question_answer_counts,
    _question_state,
    _record_skip_if_unanswered,
    finish_interview_state,
    skip_interview_question_state,
)

__all__ = [
    "INTERVIEW_FINISHED_MESSAGE",
    "finish_interview_state",
    "skip_interview_question_state",
]
