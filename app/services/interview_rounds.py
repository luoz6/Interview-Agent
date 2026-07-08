from typing import Literal

from app.graphs.interview_state import (
    InterviewState,
    count_candidate_answers_for_question,
    get_current_question,
)
from app.services.runtime_domain_events import RoundClosedEvent


AnswerState = Literal["answered", "skipped", "unanswered"]


def round_closed_event_from_transition(
    before_state: InterviewState,
    after_state: InterviewState,
) -> RoundClosedEvent | None:
    closed_question = get_current_question(before_state)
    if closed_question is None:
        return None

    after_current = get_current_question(after_state)
    if (
        after_state["status"] != "finished"
        and after_current is not None
        and after_current.id == closed_question.id
    ):
        return None

    return RoundClosedEvent(
        session_id=after_state["session_id"],
        question_id=closed_question.id,
        answer_state=_answer_state_for_question(after_state, closed_question.id),
        job_tags=list(after_state["job_tags"]),
    )


def _answer_state_for_question(
    state: InterviewState,
    question_id: str,
) -> AnswerState:
    if question_id in state.get("skipped_question_ids", []):
        return "skipped"
    if count_candidate_answers_for_question(state, question_id) > 0:
        return "answered"
    return "unanswered"
