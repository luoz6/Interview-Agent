from collections.abc import Callable
from typing import Literal

from app.agents.shadow_reviewer import ShadowReviewerAgent
from app.services.question_evaluations import (
    QuestionEvaluationRecord,
    question_evaluation_from_feedback,
)
from app.services.round_review import build_single_question_review_state
from app.services.runtime import get_session_store, resolve_runtime_llm
from app.services.runtime_domain_events import RoundClosedEvent
from app.services.vector_store import get_knowledge_store


def run_round_review_event_payload(payload: dict) -> QuestionEvaluationRecord:
    event = RoundClosedEvent.model_validate(payload)
    store = get_session_store()
    return run_round_review_event(
        event,
        store=store,
        llm=resolve_runtime_llm(store),
        vector_store=get_knowledge_store(),
    )


def run_round_review_event(
    event: RoundClosedEvent,
    *,
    store,
    llm,
    vector_store,
    reviewer_factory: Callable | None = None,
) -> QuestionEvaluationRecord:
    state = store.get(event.session_id)
    return run_round_review_event_from_state(
        event,
        state=state,
        store=store,
        llm=llm,
        vector_store=vector_store,
        reviewer_factory=reviewer_factory,
    )


def run_round_review_event_from_state(
    event: RoundClosedEvent,
    *,
    state,
    store,
    llm,
    vector_store,
    reviewer_factory: Callable | None = None,
) -> QuestionEvaluationRecord:
    try:
        review_state = build_single_question_review_state(state, event.question_id)
        reviewer = (reviewer_factory or ShadowReviewerAgent)(
            llm=llm,
            vector_store=vector_store,
        )
        report = reviewer.evaluate(review_state)
        feedback = _select_feedback(report.feedbacks, event.question_id)
        record = question_evaluation_from_feedback(
            session_id=event.session_id,
            feedback=feedback,
            answer_state=event.answer_state,
        )
    except Exception as exc:
        record = _failed_question_evaluation(
            session_id=event.session_id,
            question_id=event.question_id,
            answer_state=event.answer_state,
            error=str(exc),
        )

    store.upsert_question_evaluation(event.session_id, record)
    return record


def _select_feedback(feedbacks, question_id: str):
    for feedback in feedbacks:
        if feedback.question_id == question_id:
            return feedback
    if not feedbacks:
        raise ValueError("round review returned no feedback")
    return feedbacks[0]


def _failed_question_evaluation(
    *,
    session_id: str,
    question_id: str,
    answer_state: Literal["answered", "skipped", "unanswered"],
    error: str,
) -> QuestionEvaluationRecord:
    return QuestionEvaluationRecord(
        session_id=session_id,
        question_id=question_id,
        answer_state=answer_state,
        status="failed",
        error=error or "round review failed",
    )
