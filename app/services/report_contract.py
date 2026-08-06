from typing import Any, Callable

from pydantic import BaseModel, Field

from app.services.report import (
    DimensionScores,
    FeedbackReference,
    InterviewFeedback,
    InterviewReport,
    REPORT_PRESENTATION_VERSION_V2,
    REPORT_SCHEMA_VERSION_V2,
    ReportCoverageV2,
    ReportEvidenceRefV2,
    ReportTechnicalAppendixV2,
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
from app.services.report_observations import aggregate_report_observations
from app.services.report_summary import build_cross_question_summary


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
    summary_provider: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
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
            highlights=result.highlights,
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
    report_dimension_evaluations = dimension_evaluations(coverage)
    report_evidence_refs = build_report_evidence_refs(feedbacks)
    observations = aggregate_report_observations(
        feedbacks=feedbacks,
        dimension_evaluations=report_dimension_evaluations,
        evidence_refs=report_evidence_refs,
    )
    report_coverage = ReportCoverageV2(
        status=coverage.coverage_status,
        evaluated_count=coverage.evaluated_count,
        total_eligible_count=coverage.total_eligible_count,
        evidence_count=coverage.evidence_count,
        per_dimension=report_dimension_evaluations,
    )
    summary_result = build_cross_question_summary(
        observations=observations,
        coverage=report_coverage,
        evidence_refs=report_evidence_refs,
        provider=summary_provider,
    )

    return InterviewReport(
        session_id=session_id,
        report_schema_version=REPORT_SCHEMA_VERSION_V2,
        presentation_version=REPORT_PRESENTATION_VERSION_V2,
        overall_score=coverage.overall_score,
        overall_dimension_scores=coverage.overall_dimension_scores,
        generation_status=(
            "degraded" if summary_result.degraded else "complete"
        ),
        generation_reason_code=summary_result.reason_code,
        score_status=coverage.score_status,
        score_reason_code=coverage.score_reason_code,
        coverage_status=coverage.coverage_status,
        evaluated_count=coverage.evaluated_count,
        total_eligible_count=coverage.total_eligible_count,
        evidence_count=coverage.evidence_count,
        scoring_rubric_version=REPORT_SCORING_RUBRIC_VERSION,
        scoring_rubric_sha256=REPORT_SCORING_RUBRIC_SHA256,
        dimension_evaluations=report_dimension_evaluations,
        question_evaluations=question_evaluations(feedbacks),
        coverage=report_coverage,
        summary_observations=summary_result.summary_observations,
        strengths=summary_result.strengths,
        limitations=summary_result.limitations,
        evidence_refs=report_evidence_refs,
        technical_appendix=ReportTechnicalAppendixV2(
            reason_codes=[coverage.score_reason_code],
            report_path="full_session",
            observations=observations,
            summary_prompt_version=summary_result.prompt_version,
            summary_prompt_sha256=summary_result.prompt_sha256,
            summary_generation_mode=summary_result.generation_mode,
            metadata={"coverage_status": coverage.coverage_status},
        ),
        summary=summary_result.summary,
        highlights=highlights,
        feedbacks=feedbacks,
    )


def build_report_evidence_refs(
    feedbacks: list[InterviewFeedback],
) -> list[ReportEvidenceRefV2]:
    refs: list[ReportEvidenceRefV2] = []
    seen: set[str] = set()
    for feedback in feedbacks:
        if feedback.answer_state == "answered" and feedback.user_answer.strip():
            evidence_ref_id = f"candidate:{feedback.question_id}:answer"
            if evidence_ref_id not in seen:
                refs.append(
                    ReportEvidenceRefV2(
                        evidence_ref_id=evidence_ref_id,
                        namespace="candidate",
                        question_id=feedback.question_id,
                        excerpt=feedback.user_answer.strip(),
                    )
                )
                seen.add(evidence_ref_id)
        for reference in feedback.references:
            evidence_ref_id = f"reference:{reference.chunk_id}"
            if evidence_ref_id in seen:
                continue
            refs.append(
                ReportEvidenceRefV2(
                    evidence_ref_id=evidence_ref_id,
                    namespace="reference",
                    question_id=feedback.question_id,
                    source_id=reference.chunk_id,
                    excerpt=reference.excerpt,
                )
            )
            seen.add(evidence_ref_id)
    return refs


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


def _short_snippet(text: str, max_length: int = 80) -> str:
    snippet = text.strip()
    if len(snippet) <= max_length:
        return snippet
    return snippet[: max_length - 3].rstrip() + "..."
