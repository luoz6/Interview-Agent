from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.domain.report.actions import (
    REPORT_ACTION_PLANNER_VERSION,
    plan_priority_actions,
)
from app.domain.report.answer_guidance import (
    ANSWER_STRUCTURE_SUGGESTION,
    REPORT_ANSWER_GUIDANCE_VERSION,
    apply_safe_answer_guidance,
    build_structure_only_guidance,
)
from app.domain.report.assembly import build_report_evidence_refs
from app.domain.report.coverage import (
    aggregate_report_coverage,
    apply_report_coverage,
    dimension_evaluations,
    populate_feedback_dimension_evaluations,
    question_evaluations,
)
from app.domain.report.evaluation_chunks import (
    EvaluationChunk,
    build_evaluation_chunks,
)
from app.domain.report.models import (
    DimensionScores,
    InterviewFeedback,
    InterviewReport,
    REPORT_PRESENTATION_VERSION_V2,
    REPORT_SCHEMA_VERSION_V2,
    ReportCoverageV2,
    ReportTechnicalAppendixV2,
)
from app.domain.report.observations import aggregate_report_observations
from app.domain.report.scoring import (
    REPORT_SCORING_RUBRIC_SHA256,
    REPORT_SCORING_RUBRIC_VERSION,
)
from app.domain.report.summary import (
    REPORT_SUMMARY_PROMPT_SHA256,
    REPORT_SUMMARY_PROMPT_VERSION,
    build_cross_question_summary,
)


def _null_dimension_scores() -> DimensionScores:
    return DimensionScores()


def build_fallback_report(
    state: Mapping[str, Any],
    chunks: list[EvaluationChunk] | None = None,
) -> InterviewReport:
    chunks = chunks if chunks is not None else build_evaluation_chunks(state)
    feedbacks = [
        InterviewFeedback(
            question_id=chunk.question_id,
            question_text=chunk.question_text,
            user_answer=_summarize_candidate_answers(chunk),
            answer_state=chunk.answer_state,
            score=None,
            dimension_scores=_null_dimension_scores(),
            evaluation_status=(
                "insufficient_evidence"
                if chunk.answer_state == "answered"
                else "not_evaluated"
            ),
            evaluation_reason_code=(
                "scoring_generation_failed"
                if chunk.answer_state == "answered"
                else chunk.answer_state
            ),
            rationale="兜底报告：本题未能生成稳定的结构化专家评估。",
            critique="AI 评估未能解析出稳定的逐题反馈。",
            better_answer=(
                "请按背景、动作、取舍、结果四段式重构回答，并补充可量化指标。"
            ),
            references=[],
        )
        for chunk in chunks
    ]
    feedbacks = populate_feedback_dimension_evaluations(feedbacks)
    coverage = aggregate_report_coverage(feedbacks)
    report_dimension_evaluations = dimension_evaluations(coverage)
    report_evidence_refs = build_report_evidence_refs(feedbacks)
    observations = aggregate_report_observations(
        feedbacks=feedbacks,
        dimension_evaluations=report_dimension_evaluations,
        evidence_refs=report_evidence_refs,
    )
    guidance_result = apply_safe_answer_guidance(
        feedbacks=feedbacks,
        observations=observations,
        evidence_refs=report_evidence_refs,
    )
    feedbacks = guidance_result.feedbacks
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
    )
    priority_actions = plan_priority_actions(
        observations=observations,
        coverage=report_coverage,
        evidence_refs=report_evidence_refs,
    )
    return InterviewReport(
        session_id=state["session_id"],
        report_schema_version=REPORT_SCHEMA_VERSION_V2,
        presentation_version=REPORT_PRESENTATION_VERSION_V2,
        overall_score=None,
        overall_dimension_scores=_null_dimension_scores(),
        generation_status="degraded",
        generation_reason_code="invalid_provider_output",
        score_status="unscored",
        score_reason_code="scoring_generation_failed",
        coverage_status="none",
        evaluated_count=0,
        total_eligible_count=sum(
            chunk.answer_state == "answered" for chunk in chunks
        ),
        evidence_count=0,
        dimension_evaluations=report_dimension_evaluations,
        question_evaluations=question_evaluations(feedbacks),
        coverage=report_coverage,
        summary_observations=summary_result.summary_observations,
        strengths=summary_result.strengths,
        priority_actions=priority_actions,
        limitations=summary_result.limitations,
        evidence_refs=report_evidence_refs,
        technical_appendix=ReportTechnicalAppendixV2(
            reason_codes=[
                "invalid_provider_output",
                coverage.score_reason_code,
            ],
            report_path="heuristic",
            observations=observations,
            summary_prompt_version=REPORT_SUMMARY_PROMPT_VERSION,
            summary_prompt_sha256=REPORT_SUMMARY_PROMPT_SHA256,
            summary_generation_mode="deterministic_fallback",
            metadata={
                "coverage_status": coverage.coverage_status,
                "action_planner_version": REPORT_ACTION_PLANNER_VERSION,
                "priority_action_count": len(priority_actions),
                "answer_guidance_version": REPORT_ANSWER_GUIDANCE_VERSION,
                "example_rewrite_published_count": (
                    guidance_result.example_rewrite_published_count
                ),
                "unsafe_rewrite_omitted_count": (
                    guidance_result.unsafe_rewrite_omitted_count
                ),
            },
        ),
        report_path="heuristic",
        scoring_rubric_version=REPORT_SCORING_RUBRIC_VERSION,
        scoring_rubric_sha256=REPORT_SCORING_RUBRIC_SHA256,
        summary=summary_result.summary,
        highlights=["已完成本次模拟面试"],
        is_fallback=True,
        feedbacks=feedbacks,
    )


def _apply_answer_state_overrides(
    report: InterviewReport,
    chunks: list[EvaluationChunk],
) -> InterviewReport:
    chunk_by_id = {chunk.question_id: chunk for chunk in chunks}
    feedbacks = []
    for feedback in report.feedbacks:
        chunk = chunk_by_id.get(feedback.question_id)
        if chunk is None or chunk.answer_state == "answered":
            feedbacks.append(feedback)
            continue
        feedbacks.append(
            build_empty_answer_feedback(
                chunk,
                references=feedback.references,
            )
        )
    return apply_report_coverage(report, feedbacks=feedbacks)


def build_empty_answer_feedback(
    chunk: EvaluationChunk,
    *,
    references=None,
) -> InterviewFeedback:
    skipped = chunk.answer_state == "skipped"
    return InterviewFeedback(
        question_id=chunk.question_id,
        question_text=chunk.question_text,
        user_answer=(
            "候选人跳过了这道题。" if skipped else "候选人未作答这道题。"
        ),
        answer_state=chunk.answer_state,
        score=None,
        dimension_scores=_null_dimension_scores(),
        evaluation_status="not_evaluated",
        evaluation_reason_code=chunk.answer_state,
        rationale=(
            "候选人跳过了这道题。" if skipped else "候选人未作答这道题。"
        ),
        critique="当前没有可评估的候选人回答。",
        better_answer=build_structure_only_guidance(),
        answer_structure_suggestion=ANSWER_STRUCTURE_SUGGESTION,
        references=list(references or []),
    )


def _summarize_candidate_answers(chunk: EvaluationChunk) -> str:
    if chunk.answer_state == "skipped":
        return "候选人跳过了这道题。"
    answers = [
        message["content"].strip()
        for message in chunk.messages
        if message["role"] == "candidate" and message["content"].strip()
    ]
    if not answers:
        return "候选人未作答这道题。"
    return " ".join(answers)


__all__ = [
    "_apply_answer_state_overrides",
    "_null_dimension_scores",
    "_summarize_candidate_answers",
    "build_empty_answer_feedback",
    "build_fallback_report",
]
