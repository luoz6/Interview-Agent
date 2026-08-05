from pydantic import BaseModel, Field

from app.services.report import (
    DimensionScores,
    FeedbackReference,
    InterviewFeedback,
    InterviewReport,
)
from app.services.report_coverage import (
    aggregate_report_coverage,
    dimension_evaluations,
    populate_feedback_dimension_evaluations,
    question_evaluations,
)
from app.services.report_rule_score import (
    REPORT_SCORING_RUBRIC_SHA256,
    REPORT_SCORING_RUBRIC_VERSION,
)


class CanonicalQuestionResult(BaseModel):
    question_id: str
    question_text: str
    user_answer: str
    score: int | None = Field(default=None, ge=0, le=100)
    dimension_scores: DimensionScores
    evaluation_status: str = "evaluated"
    evaluation_reason_code: str = "sufficient_evidence"
    evidence_count: int = Field(default=0, ge=0)
    applicable_dimensions: list[str] = Field(default_factory=list)
    dimension_evidence: list[dict] = Field(default_factory=list)
    rationale: str
    critique: str
    better_answer: str
    reference_chunk_ids: list[str]
    highlights: list[str] = Field(default_factory=list)


def assemble_interview_report(
    *,
    session_id: str,
    question_results: list[CanonicalQuestionResult],
    reference_lookup: dict[str, dict[str, str]],
) -> InterviewReport:
    if not question_results:
        raise ValueError("question_results must not be empty")

    feedbacks = [
        InterviewFeedback(
            question_id=result.question_id,
            question_text=result.question_text,
            user_answer=result.user_answer,
            score=result.score,
            dimension_scores=result.dimension_scores,
            evaluation_status=result.evaluation_status,
            evaluation_reason_code=result.evaluation_reason_code,
            evidence_count=result.evidence_count,
            applicable_dimensions=result.applicable_dimensions,
            dimension_evidence=result.dimension_evidence,
            rationale=result.rationale,
            critique=result.critique,
            better_answer=result.better_answer,
            references=[
                FeedbackReference(**reference_lookup[chunk_id])
                for chunk_id in result.reference_chunk_ids
                if chunk_id in reference_lookup
            ],
        )
        for result in question_results
    ]

    feedbacks = populate_feedback_dimension_evaluations(feedbacks)
    coverage = aggregate_report_coverage(feedbacks)

    highlights = _build_highlights(question_results)
    summary = _build_summary(question_results, highlights)

    return InterviewReport(
        session_id=session_id,
        overall_score=coverage.overall_score,
        overall_dimension_scores=coverage.overall_dimension_scores,
        score_status=coverage.score_status,
        score_reason_code=coverage.score_reason_code,
        coverage_status=coverage.coverage_status,
        evaluated_count=coverage.evaluated_count,
        total_eligible_count=coverage.total_eligible_count,
        evidence_count=coverage.evidence_count,
        scoring_rubric_version=REPORT_SCORING_RUBRIC_VERSION,
        scoring_rubric_sha256=REPORT_SCORING_RUBRIC_SHA256,
        dimension_evaluations=dimension_evaluations(coverage),
        question_evaluations=question_evaluations(feedbacks),
        summary=summary,
        highlights=highlights,
        feedbacks=feedbacks,
    )


def _build_highlights(question_results: list[CanonicalQuestionResult]) -> list[str]:
    highlights: list[str] = []
    for result in question_results:
        for highlight in result.highlights:
            text = highlight.strip()
            if text and text not in highlights:
                highlights.append(text)
                if len(highlights) == 3:
                    return highlights

    if highlights:
        return highlights

    return [_short_snippet(result.critique) for result in question_results[:3]]


def _build_summary(
    question_results: list[CanonicalQuestionResult],
    highlights: list[str],
) -> str:
    if highlights:
        return " ".join(highlights)
    return " ".join(result.rationale for result in question_results[:2]).strip()


def _short_snippet(text: str, max_length: int = 80) -> str:
    snippet = text.strip()
    if len(snippet) <= max_length:
        return snippet
    return snippet[: max_length - 3].rstrip() + "..."
