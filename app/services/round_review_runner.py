from collections.abc import Callable
from typing import Literal

from app.agents.shadow_reviewer import ShadowReviewerAgent
from app.services.agent_runtime import (
    AgentExecutionContext,
    AgentExecutionRunner,
    evidence_ids_for_question,
)
from app.services.evaluator import (
    build_empty_answer_feedback,
    build_evaluation_chunks,
)
from app.services.question_evaluations import (
    QuestionEvaluationRecord,
    question_evaluation_from_feedback,
)
from app.services.round_review import build_single_question_review_state
from app.services.runtime import get_session_store, resolve_runtime_llm
from app.services.runtime_domain_events import RoundClosedEvent
from app.services.vector_store import get_knowledge_store


def run_round_review_event_payload(
    payload: dict,
    *,
    execution_runner: AgentExecutionRunner | None = None,
) -> QuestionEvaluationRecord:
    event = RoundClosedEvent.model_validate(payload)
    store = get_session_store()
    if event.answer_state in {"skipped", "unanswered"}:
        return run_round_review_event(
            event,
            store=store,
            llm=None,
            vector_store=None,
            execution_runner=execution_runner,
        )
    return run_round_review_event(
        event,
        store=store,
        llm=resolve_runtime_llm(store),
        vector_store=get_knowledge_store(),
        execution_runner=execution_runner,
    )


def run_round_review_event(
    event: RoundClosedEvent,
    *,
    store,
    llm,
    vector_store,
    reviewer_factory: Callable | None = None,
    execution_runner: AgentExecutionRunner | None = None,
) -> QuestionEvaluationRecord:
    state = store.get(event.session_id)
    return run_round_review_event_from_state(
        event,
        state=state,
        store=store,
        llm=llm,
        vector_store=vector_store,
        reviewer_factory=reviewer_factory,
        execution_runner=execution_runner,
    )


def run_round_review_event_from_state(
    event: RoundClosedEvent,
    *,
    state,
    store,
    llm,
    vector_store,
    reviewer_factory: Callable | None = None,
    execution_runner: AgentExecutionRunner | None = None,
    attempt_number: int = 1,
) -> QuestionEvaluationRecord:
    try:
        record = evaluate_round_review_event(
            event,
            state=state,
            llm=llm,
            vector_store=vector_store,
            reviewer_factory=reviewer_factory,
            execution_runner=execution_runner,
            attempt_number=attempt_number,
        )
    except Exception as exc:
        record = failed_question_evaluation(
            session_id=event.session_id,
            question_id=event.question_id,
            answer_state=event.answer_state,
            error=str(exc),
        )

    store.upsert_question_evaluation(event.session_id, record)
    return record


def evaluate_round_review_event(
    event: RoundClosedEvent,
    *,
    state,
    llm,
    vector_store,
    reviewer_factory: Callable | None = None,
    execution_runner: AgentExecutionRunner | None = None,
    attempt_number: int = 1,
) -> QuestionEvaluationRecord:
    review_state = build_single_question_review_state(
        state,
        event.question_id,
    )
    if event.answer_state in {"skipped", "unanswered"}:
        chunk = _select_evaluation_chunk(
            build_evaluation_chunks(review_state),
            event.question_id,
        ).model_copy(update={"answer_state": event.answer_state})
        feedback = build_empty_answer_feedback(chunk)
        retrieval_metadata = {
            "retrieval_path": "not_applicable",
            "degraded_reason": f"answer_state_{event.answer_state}",
        }
    else:
        reviewer = (reviewer_factory or ShadowReviewerAgent)(
            llm=llm,
            vector_store=vector_store,
        )
        runner = execution_runner or AgentExecutionRunner()
        report = runner.run(
            _review_context(
                event,
                state,
                attempt_number=attempt_number,
            ),
            lambda: reviewer.evaluate(review_state),
        )
        feedback = _select_feedback(report.feedbacks, event.question_id)
        retrieval_metadata = getattr(
            reviewer,
            "last_retrieval_by_question",
            {},
        ).get(event.question_id, {})
    return question_evaluation_from_feedback(
        session_id=event.session_id,
        feedback=feedback,
        answer_state=event.answer_state,
        retrieval_path=retrieval_metadata.get("retrieval_path"),
        degraded_reason=retrieval_metadata.get("degraded_reason"),
        evidence_content_sha256=retrieval_metadata.get(
            "evidence_content_sha256"
        ),
    )


def _review_context(
    event: RoundClosedEvent,
    state,
    *,
    attempt_number: int,
) -> AgentExecutionContext:
    return AgentExecutionContext(
        correlation_id=event.correlation_id or event.session_id,
        causation_id=event.event_id,
        agent="shadow_reviewer",
        operation="evaluate_round",
        phase="review",
        session_id=event.session_id,
        question_id=event.question_id,
        state_version=event.state_version,
        command_id=event.causation_id,
        evidence_ids=evidence_ids_for_question(
            state["plan"],
            event.question_id,
        ),
        attempt_number=attempt_number,
    )


def _select_feedback(feedbacks, question_id: str):
    for feedback in feedbacks:
        if feedback.question_id == question_id:
            return feedback
    if not feedbacks:
        raise ValueError("round review returned no feedback")
    return feedbacks[0]


def _select_evaluation_chunk(chunks, question_id: str):
    for chunk in chunks:
        if chunk.question_id == question_id:
            return chunk
    raise ValueError("round review question was not found")


def failed_question_evaluation(
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
