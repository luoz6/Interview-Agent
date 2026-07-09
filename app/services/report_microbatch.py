import logging
from typing import Literal, cast

from app.agents.report_coach import ReportCoachAgent
from app.services.evaluator import build_evaluation_chunks
from app.services.question_evaluations import QuestionEvaluationRecord
from app.services.report import DimensionScores, InterviewReport, ReportProgress
from app.services.round_review_runner import run_round_review_event_from_state
from app.services.runtime_domain_events import RoundClosedEvent


AnswerState = Literal["answered", "skipped", "unanswered"]
logger = logging.getLogger(__name__)


class MicrobatchReportUnavailable(RuntimeError):
    """Raised when question-level microbatches cannot produce a complete report input."""


def generate_microbatch_report(
    state,
    *,
    store,
    llm,
    vector_store,
    on_progress=None,
    reviewer_factory=None,
):
    if on_progress is not None:
        on_progress(
            ReportProgress(
                stage="retrieving",
                percent=20,
                message="Loading question-level review microbatches.",
            )
        )

    records = ensure_completed_question_evaluations_for_report(
        state,
        store=store,
        llm=llm,
        vector_store=vector_store,
        reviewer_factory=reviewer_factory,
    )

    if on_progress is not None:
        on_progress(
            ReportProgress(
                stage="analyzing",
                percent=60,
                message="Reusing completed question-level review scores.",
                current_question_id=records[0].question_id if records else None,
            )
        )

    coach_report = ReportCoachAgent(llm=llm).generate_report(
        plan=state["plan"],
        evaluation_items=build_report_coach_items_from_question_evaluations(records),
        session_id=state["session_id"],
    )
    report = finalize_report_with_microbatch_feedback(coach_report, records)

    if on_progress is not None:
        on_progress(
            ReportProgress(
                stage="aggregating",
                percent=80,
                message="Aggregating microbatch review scores.",
            )
        )
        on_progress(
            ReportProgress(
                stage="completed",
                percent=100,
                message="Microbatch-backed report completed.",
            )
        )

    return report


def ensure_completed_question_evaluations_for_report(
    state,
    *,
    store,
    llm,
    vector_store,
    reviewer_factory=None,
) -> list[QuestionEvaluationRecord]:
    session_id = state["session_id"]
    existing_by_question_id = {
        record.question_id: record
        for record in store.list_question_evaluations(session_id)
    }
    chunks = build_evaluation_chunks(state)
    records: list[QuestionEvaluationRecord] = []

    for chunk in chunks:
        record = existing_by_question_id.get(chunk.question_id)
        if _is_completed_record(record):
            records.append(record)
            continue

        reviewed = run_round_review_event_from_state(
            RoundClosedEvent(
                session_id=session_id,
                question_id=chunk.question_id,
                answer_state=_coerce_answer_state(chunk.answer_state),
                job_tags=list(state["job_tags"]),
            ),
            state=state,
            store=store,
            llm=llm,
            vector_store=vector_store,
            reviewer_factory=reviewer_factory,
        )
        if not _is_completed_record(reviewed):
            reason = reviewed.error if reviewed is not None else "missing feedback"
            raise MicrobatchReportUnavailable(
                f"question review unavailable for {chunk.question_id}: {reason}"
            )
        records.append(reviewed)

    if not records:
        raise MicrobatchReportUnavailable("no question evaluations available")
    return records


def finalize_report_with_microbatch_feedback(
    report: InterviewReport,
    records: list[QuestionEvaluationRecord],
) -> InterviewReport:
    feedbacks = [
        record.feedback
        for record in records
        if record.status == "completed" and record.feedback is not None
    ]
    if len(feedbacks) != len(records):
        raise MicrobatchReportUnavailable("microbatch report feedback is incomplete")
    return report.model_copy(
        update={
            "feedbacks": feedbacks,
            "overall_score": _average_score(feedbacks),
            "overall_dimension_scores": _average_dimension_scores(feedbacks),
        }
    )


def build_report_coach_items_from_question_evaluations(
    records: list[QuestionEvaluationRecord],
) -> list[dict]:
    items = []
    for record in records:
        if not _is_completed_record(record):
            raise MicrobatchReportUnavailable(
                f"question evaluation is not reusable: {record.question_id}"
            )
        feedback = record.feedback
        references = [reference.model_dump() for reference in feedback.references]
        items.append(
            {
                "source": "question_evaluation_record",
                "question_id": feedback.question_id,
                "question_text": feedback.question_text,
                "answer_state": record.answer_state,
                "user_answer": feedback.user_answer,
                "microbatch_score": feedback.score,
                "score": feedback.score,
                "dimension_scores": feedback.dimension_scores.model_dump(),
                "rationale": feedback.rationale,
                "critique": feedback.critique,
                "better_answer": feedback.better_answer,
                "scoring_references": references,
                "answer_references": references,
                "messages": [
                    {"role": "candidate", "content": feedback.user_answer},
                    {"role": "reviewer", "content": feedback.rationale},
                    {"role": "reviewer", "content": feedback.critique},
                ],
            }
        )
    return items


def _is_completed_record(record: QuestionEvaluationRecord | None) -> bool:
    return (
        record is not None
        and record.status == "completed"
        and record.feedback is not None
    )


def _coerce_answer_state(value: str) -> AnswerState:
    if value in {"answered", "skipped", "unanswered"}:
        return cast(AnswerState, value)
    logger.warning(
        "unknown answer_state for question review microbatch",
        extra={"answer_state": value},
    )
    return "unanswered"


def _average_score(feedbacks) -> int:
    if not feedbacks:
        return 0
    return round(sum(feedback.score for feedback in feedbacks) / len(feedbacks))


def _average_dimension_scores(feedbacks) -> DimensionScores:
    if not feedbacks:
        return DimensionScores(
            breadth=0,
            depth=0,
            architecture=0,
            engineering=0,
            communication=0,
        )
    fields = DimensionScores.model_fields.keys()
    values = {
        field: round(
            sum(getattr(feedback.dimension_scores, field) for feedback in feedbacks)
            / len(feedbacks)
        )
        for field in fields
    }
    return DimensionScores(**values)
