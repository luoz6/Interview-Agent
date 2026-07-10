from pydantic import BaseModel, Field

from app.services.report import (
    DimensionScores,
    FeedbackReference,
    InterviewFeedback,
    InterviewReport,
)
from app.services.report_rule_score import aggregate_feedback_scores


class CanonicalQuestionResult(BaseModel):
    question_id: str
    question_text: str
    user_answer: str
    score: int = Field(ge=0, le=100)
    dimension_scores: DimensionScores
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

    overall_score, overall_dimension_scores = aggregate_feedback_scores(feedbacks)

    highlights = _build_highlights(question_results)
    summary = _build_summary(question_results, highlights)

    return InterviewReport(
        session_id=session_id,
        overall_score=overall_score,
        overall_dimension_scores=overall_dimension_scores,
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
