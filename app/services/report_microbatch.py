import logging
from typing import Literal, cast

from pydantic import BaseModel, Field

from app.agents.report_coach import ReportCoachAgent
from app.services.agent_runtime import (
    AgentExecutionContext,
    AgentExecutionRunner,
    correlation_id_from_plan,
)
from app.services.evaluator import build_evaluation_chunks
from app.services.question_evaluations import QuestionEvaluationRecord
from app.services.report import DimensionScores, InterviewReport, ReportProgress
from app.services.report_rule_score import aggregate_feedback_scores
from app.services.round_review_runner import run_round_review_event_from_state
from app.services.runtime_domain_events import RoundClosedEvent


AnswerState = Literal["answered", "skipped", "unanswered"]
logger = logging.getLogger(__name__)


class ReportMicrobatchStats(BaseModel):
    report_path: str = "microbatch"
    total_questions: int = 0
    reused_questions: int = 0
    rerun_questions: int = 0
    failed_questions: int = 0
    question_ids: list[str] = Field(default_factory=list)
    reused_question_ids: list[str] = Field(default_factory=list)
    rerun_question_ids: list[str] = Field(default_factory=list)
    failed_question_ids: list[str] = Field(default_factory=list)

    def to_metadata(self) -> dict:
        return {
            "report_path": self.report_path,
            "microbatch_total_questions": self.total_questions,
            "microbatch_reused_questions": self.reused_questions,
            "microbatch_rerun_questions": self.rerun_questions,
            "microbatch_failed_questions": self.failed_questions,
            "question_ids": list(self.question_ids),
            "reused_question_ids": list(self.reused_question_ids),
            "rerun_question_ids": list(self.rerun_question_ids),
            "failed_question_ids": list(self.failed_question_ids),
        }


class MicrobatchReportUnavailable(RuntimeError):
    """Raised when question-level microbatches cannot produce a complete report input."""

    def __init__(
        self,
        message: str,
        *,
        stats: ReportMicrobatchStats | None = None,
    ) -> None:
        super().__init__(message)
        self.stats = stats


def generate_microbatch_report(
    state,
    *,
    store,
    llm,
    vector_store,
    on_progress=None,
    on_microbatch_stats=None,
    reviewer_factory=None,
    execution_runner: AgentExecutionRunner | None = None,
    attempt_number: int = 1,
):
    if on_progress is not None:
        on_progress(
            ReportProgress(
                stage="retrieving",
                percent=20,
                message="Loading question-level review microbatches.",
            )
        )

    stats = ReportMicrobatchStats()
    try:
        records = ensure_completed_question_evaluations_for_report(
            state,
            store=store,
            llm=llm,
            vector_store=vector_store,
            reviewer_factory=reviewer_factory,
            execution_runner=execution_runner,
            attempt_number=attempt_number,
            stats=stats,
        )
    except MicrobatchReportUnavailable as exc:
        if exc.stats is None:
            exc.stats = stats
        if on_microbatch_stats is not None:
            on_microbatch_stats(stats)
        raise
    else:
        if on_microbatch_stats is not None:
            on_microbatch_stats(stats)

    if on_progress is not None:
        on_progress(
            ReportProgress(
                stage="analyzing",
                percent=60,
                message="Reusing completed question-level review scores.",
                current_question_id=records[0].question_id if records else None,
                metadata=stats.to_metadata(),
            )
        )

    chunks_by_question_id = {
        chunk.question_id: chunk
        for chunk in build_evaluation_chunks(state)
    }
    command_id = state.get("last_command_id")
    evidence_ids = [
        reference.chunk_id
        for record in records
        if record.feedback is not None
        for reference in record.feedback.references
    ]
    coach_report = ReportCoachAgent(
        llm=llm,
        execution_runner=execution_runner,
    ).generate_report(
        plan=state["plan"],
        evaluation_items=build_report_coach_items_from_question_evaluations(
            records,
            chunks_by_question_id=chunks_by_question_id,
        ),
        session_id=state["session_id"],
        execution_context=AgentExecutionContext(
            correlation_id=correlation_id_from_plan(
                state["plan"],
                session_id=state["session_id"],
            ),
            causation_id=command_id,
            agent="report_coach",
            operation="generate_microbatch_report",
            phase="review",
            session_id=state["session_id"],
            state_version=state["state_version"],
            command_id=command_id,
            evidence_ids=evidence_ids,
            attempt_number=attempt_number,
        ),
        trace_metadata={
            "question_count": len(records),
            "report_path": "microbatch",
        },
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
    execution_runner: AgentExecutionRunner | None = None,
    attempt_number: int = 1,
    stats: ReportMicrobatchStats | None = None,
) -> list[QuestionEvaluationRecord]:
    session_id = state["session_id"]
    existing_by_question_id = {
        record.question_id: record
        for record in store.list_question_evaluations(session_id)
    }
    chunks = build_evaluation_chunks(state)
    if stats is not None:
        stats.total_questions = len(chunks)
        stats.question_ids = [chunk.question_id for chunk in chunks]
    records: list[QuestionEvaluationRecord] = []

    for chunk in chunks:
        record = existing_by_question_id.get(chunk.question_id)
        if _is_completed_record(record):
            if stats is not None:
                stats.reused_questions += 1
                stats.reused_question_ids.append(chunk.question_id)
            records.append(record)
            continue

        if stats is not None:
            stats.rerun_questions += 1
            stats.rerun_question_ids.append(chunk.question_id)
        reviewed = run_round_review_event_from_state(
            RoundClosedEvent(
                session_id=session_id,
                correlation_id=correlation_id_from_plan(
                    state["plan"],
                    session_id=session_id,
                ),
                causation_id=state.get("last_command_id"),
                state_version=state["state_version"],
                question_id=chunk.question_id,
                answer_state=_coerce_answer_state(chunk.answer_state),
                job_tags=list(state["job_tags"]),
            ),
            state=state,
            store=store,
            llm=llm,
            vector_store=vector_store,
            reviewer_factory=reviewer_factory,
            execution_runner=execution_runner,
            attempt_number=attempt_number,
        )
        if not _is_completed_record(reviewed):
            reason = reviewed.error if reviewed is not None else "missing feedback"
            if stats is not None:
                stats.failed_questions += 1
                stats.failed_question_ids.append(chunk.question_id)
            raise MicrobatchReportUnavailable(
                f"question review unavailable for {chunk.question_id}: {reason}",
                stats=stats,
            )
        records.append(reviewed)

    if not records:
        raise MicrobatchReportUnavailable("no question evaluations available", stats=stats)
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
    overall_score, overall_dimension_scores = aggregate_feedback_scores(feedbacks)
    summary = report.summary
    if not any("\u4e00" <= char <= "\u9fff" for char in summary):
        summary = (
            f"本次面试共评估 {len(feedbacks)} 道题，"
            f"后端规则聚合得分为 {overall_score} 分。"
        )
    return report.model_copy(
        update={
            "summary": summary,
            "feedbacks": feedbacks,
            "overall_score": overall_score,
            "overall_dimension_scores": overall_dimension_scores,
        }
    )


def build_report_coach_items_from_question_evaluations(
    records: list[QuestionEvaluationRecord],
    chunks_by_question_id: dict[str, object] | None = None,
) -> list[dict]:
    items = []
    for record in records:
        if not _is_completed_record(record):
            raise MicrobatchReportUnavailable(
                f"question evaluation is not reusable: {record.question_id}"
            )
        feedback = record.feedback
        references = [reference.model_dump() for reference in feedback.references]
        chunk = (chunks_by_question_id or {}).get(feedback.question_id)
        question_kind = getattr(chunk, "question_kind", "")
        items.append(
            {
                "source": "question_evaluation_record",
                "question_id": feedback.question_id,
                "question_text": feedback.question_text,
                "question_kind": question_kind,
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
    if record is None or record.status != "completed" or record.feedback is None:
        return False
    if record.answer_state != "answered":
        return True
    return bool(
        record.feedback.applicable_dimensions
        and record.feedback.dimension_evidence
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
