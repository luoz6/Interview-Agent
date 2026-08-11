from __future__ import annotations

from collections.abc import Iterable
from typing import Literal

from pydantic import BaseModel, Field

from app.services.evaluator import build_evaluation_chunks
from app.services.question_evaluations import QuestionEvaluationRecord
from app.services.report import InterviewReport


class ReportReliability(BaseModel):
    planned_question_count: int = Field(ge=0)
    answered_question_count: int = Field(ge=0)
    skipped_question_count: int = Field(ge=0)
    unanswered_question_count: int = Field(ge=0)
    reviewed_answer_count: int = Field(ge=0)
    review_failed_answer_count: int = Field(ge=0)
    evidence_bound_question_count: int = Field(ge=0)
    degraded_question_count: int = Field(ge=0)
    generation_path: Literal["structured", "mixed", "fallback"]
    degraded_reasons: list[str] = Field(default_factory=list)
    score_applicability: Literal["normal", "limited", "insufficient"]


def _project_report_reliability(
    state,
    report: InterviewReport,
    evaluations: Iterable[QuestionEvaluationRecord],
    *,
    report_path: str | None = None,
) -> ReportReliability:
    chunks = build_evaluation_chunks(state)
    answer_state_by_question = {
        chunk.question_id: chunk.answer_state for chunk in chunks
    }
    answered_ids = {
        question_id
        for question_id, answer_state in answer_state_by_question.items()
        if answer_state == "answered"
    }
    skipped_count = sum(
        answer_state == "skipped" for answer_state in answer_state_by_question.values()
    )
    unanswered_count = sum(
        answer_state == "unanswered"
        for answer_state in answer_state_by_question.values()
    )

    records = {
        record.question_id: record
        for record in evaluations
        if record.question_id in answered_ids and record.answer_state == "answered"
    }
    reviewed_ids = {
        question_id
        for question_id, record in records.items()
        if record.status == "completed" and record.feedback is not None
    }
    failed_ids = {
        question_id
        for question_id, record in records.items()
        if record.status == "failed"
    }
    degraded_ids = {
        question_id
        for question_id, record in records.items()
        if record.status == "failed"
        or bool(record.degraded_reason)
        or record.retrieval_path == "degraded"
    }
    if report.is_fallback:
        # A fallback report bypasses the complete structured review path for
        # every usable answer, even when no per-question failure record exists.
        degraded_ids.update(answered_ids)
    evidence_ids = {
        feedback.question_id
        for feedback in report.feedbacks
        if feedback.question_id in answered_ids and feedback.references
    }

    reasons: list[str] = []
    if report.is_fallback:
        reasons.append("REPORT_FALLBACK")
    if report_path == "full_session_fallback":
        reasons.append("QUESTION_REVIEW_FALLBACK")
    if failed_ids:
        reasons.append("QUESTION_REVIEW_UNAVAILABLE")
    if (
        len(reviewed_ids) < len(answered_ids)
        and not failed_ids
        and not report.is_fallback
    ):
        reasons.append("QUESTION_REVIEW_INCOMPLETE")
    if any(
        bool(record.degraded_reason) or record.retrieval_path == "degraded"
        for record in records.values()
    ):
        reasons.append("KNOWLEDGE_RETRIEVAL_DEGRADED")

    if report.is_fallback:
        generation_path = "fallback"
    elif (
        report_path == "microbatch"
        and len(reviewed_ids) == len(answered_ids)
        and not degraded_ids
    ):
        generation_path = "structured"
    else:
        generation_path = "mixed"

    planned_count = len(answer_state_by_question)
    answered_count = len(answered_ids)
    if (
        answered_count == 0
        or report.is_fallback
        or len(reviewed_ids) == 0
        or answered_count * 2 < planned_count
    ):
        score_applicability = "insufficient"
    elif (
        generation_path != "structured"
        or len(reviewed_ids) < answered_count
        or failed_ids
        or degraded_ids
    ):
        score_applicability = "limited"
    else:
        score_applicability = "normal"

    return ReportReliability(
        planned_question_count=planned_count,
        answered_question_count=answered_count,
        skipped_question_count=skipped_count,
        unanswered_question_count=unanswered_count,
        reviewed_answer_count=len(reviewed_ids),
        review_failed_answer_count=len(failed_ids),
        evidence_bound_question_count=len(evidence_ids),
        degraded_question_count=len(degraded_ids),
        generation_path=generation_path,
        degraded_reasons=reasons,
        score_applicability=score_applicability,
    )


class ReportReliabilityProjector:
    def project(
        self,
        state,
        report: InterviewReport,
        evaluations: Iterable[QuestionEvaluationRecord],
        *,
        report_path: str | None = None,
    ) -> ReportReliability:
        return _project_report_reliability(
            state,
            report,
            evaluations,
            report_path=report_path,
        )
