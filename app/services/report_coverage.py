from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from pydantic import BaseModel, ConfigDict, Field

from app.services.report import (
    DimensionScores,
    InterviewFeedback,
    InterviewReport,
    REPORT_SCHEMA_VERSION_V2,
    ReportCoverageV2,
    ScoreEvaluation,
)


DIMENSION_NAMES = (
    "breadth",
    "depth",
    "architecture",
    "engineering",
    "communication",
)


class DimensionCoverage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    status: str
    reason_code: str
    score: int | None = Field(default=None, ge=0, le=100)
    evaluated_count: int = Field(ge=0)
    eligible_count: int = Field(ge=0)
    evidence_count: int = Field(ge=0)


class ReportCoverageResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    score_status: str
    score_reason_code: str
    coverage_status: str
    overall_score: int | None = Field(default=None, ge=0, le=100)
    overall_dimension_scores: DimensionScores
    evaluated_count: int = Field(ge=0)
    total_eligible_count: int = Field(ge=0)
    evidence_count: int = Field(ge=0)
    dimensions: dict[str, DimensionCoverage]
    strongest_dimensions: list[str]
    weakest_dimensions: list[str]


def aggregate_report_coverage(feedbacks: list[InterviewFeedback]) -> ReportCoverageResult:
    items = list(feedbacks)
    eligible = [item for item in items if _answer_state(item) == "answered"]
    evaluated = [
        item
        for item in eligible
        if _evaluation_status(item) == "evaluated" and item.score is not None
    ]
    total_eligible = len(eligible)
    evaluated_count = len(evaluated)
    overall = _mean([item.score for item in evaluated if item.score is not None])
    dimension_results: dict[str, DimensionCoverage] = {}
    dimension_values: dict[str, int | None] = {}
    for dimension in DIMENSION_NAMES:
        dimension_eligible = [
            item
            for item in eligible
            if not _applicable_dimensions(item)
            or dimension in _applicable_dimensions(item)
        ]
        values = [
            getattr(item.dimension_scores, dimension)
            for item in dimension_eligible
            if _evaluation_status(item) == "evaluated"
            and getattr(item.dimension_scores, dimension) is not None
        ]
        evidence_count = sum(
            _dimension_evidence_count(item, dimension) for item in dimension_eligible
        )
        score = _mean(values)
        dimension_values[dimension] = score
        if values:
            status = "evaluated"
            reason_code = (
                "sufficient_evidence"
                if len(values) == len(dimension_eligible)
                else "partial_evidence"
            )
        elif dimension_eligible:
            status = "insufficient_evidence"
            reason_code = "insufficient_evidence"
        else:
            status = "not_evaluated"
            reason_code = "not_applicable"
        dimension_results[dimension] = DimensionCoverage(
            status=status,
            reason_code=reason_code,
            score=score,
            evaluated_count=len(values),
            eligible_count=len(dimension_eligible),
            evidence_count=evidence_count,
        )
    evidence_count = sum(_feedback_evidence_count(item) for item in evaluated)
    if evaluated_count == 0:
        score_status = "unscored"
        coverage_status = "none"
        reason = "no_answered_questions" if total_eligible == 0 else "insufficient_evidence"
    elif evaluated_count < total_eligible:
        score_status = "partial"
        coverage_status = "partial"
        reason = "partial_evidence"
    else:
        score_status = "scored"
        coverage_status = "complete"
        reason = "sufficient_evidence"
    ranked = sorted(
        (
            (name, result.score)
            for name, result in dimension_results.items()
            if result.status == "evaluated" and result.score is not None
        ),
        key=lambda item: (item[1], item[0]),
    )
    return ReportCoverageResult(
        score_status=score_status,
        score_reason_code=reason,
        coverage_status=coverage_status,
        overall_score=overall,
        overall_dimension_scores=DimensionScores(**dimension_values),
        evaluated_count=evaluated_count,
        total_eligible_count=total_eligible,
        evidence_count=evidence_count,
        dimensions=dimension_results,
        weakest_dimensions=[name for name, _ in ranked[:2]],
        strongest_dimensions=[name for name, _ in reversed(ranked[-2:])],
    )


def question_evaluations(
    feedbacks: list[InterviewFeedback],
) -> dict[str, ScoreEvaluation]:
    evaluations: dict[str, ScoreEvaluation] = {}
    for item in feedbacks:
        answer_state = _answer_state(item)
        status = _evaluation_status(item)
        score = item.score if status == "evaluated" else None
        if answer_state != "answered":
            status = "not_evaluated"
            reason = answer_state
        else:
            reason = str(
                getattr(item, "evaluation_reason_code", None)
                or ("sufficient_evidence" if status == "evaluated" else "insufficient_evidence")
            )
        evaluations[item.question_id] = ScoreEvaluation(
            status=status,
            reason_code=reason,
            score=score,
            evidence_count=_feedback_evidence_count(item),
            eligible_count=1 if answer_state == "answered" else 0,
            evaluated_count=1 if status == "evaluated" else 0,
        )
    return evaluations


def populate_feedback_dimension_evaluations(
    feedbacks: list[InterviewFeedback],
) -> list[InterviewFeedback]:
    populated: list[InterviewFeedback] = []
    for item in feedbacks:
        answer_state = _answer_state(item)
        status = _evaluation_status(item)
        applicable = set(_applicable_dimensions(item) or DIMENSION_NAMES)
        evaluations: dict[str, ScoreEvaluation] = {}
        for dimension in DIMENSION_NAMES:
            score = getattr(item.dimension_scores, dimension)
            evidence_count = _dimension_evidence_count(item, dimension)
            if dimension not in applicable:
                dimension_status = "not_evaluated"
                reason = "not_applicable"
                score = None
            elif answer_state != "answered":
                dimension_status = "not_evaluated"
                reason = answer_state
                score = None
            elif status == "evaluated" and score is not None:
                dimension_status = "evaluated"
                reason = "sufficient_evidence"
            else:
                dimension_status = "insufficient_evidence"
                reason = str(
                    getattr(item, "evaluation_reason_code", None)
                    or "insufficient_evidence"
                )
                score = None
            evaluations[dimension] = ScoreEvaluation(
                status=dimension_status,
                reason_code=reason,
                score=score,
                evidence_count=evidence_count,
                eligible_count=(
                    1
                    if dimension in applicable and answer_state == "answered"
                    else 0
                ),
                evaluated_count=1 if dimension_status == "evaluated" else 0,
            )
        populated.append(item.model_copy(update={"dimension_evaluations": evaluations}))
    return populated


def dimension_evaluations(
    coverage: ReportCoverageResult,
) -> dict[str, ScoreEvaluation]:
    return {
        name: ScoreEvaluation(
            status=result.status,
            reason_code=result.reason_code,
            score=result.score,
            evidence_count=result.evidence_count,
            eligible_count=result.eligible_count,
            evaluated_count=result.evaluated_count,
        )
        for name, result in coverage.dimensions.items()
    }


def apply_report_coverage(
    report: InterviewReport,
    *,
    feedbacks: list[InterviewFeedback] | None = None,
    report_path: str | None = None,
) -> InterviewReport:
    resolved_feedbacks = populate_feedback_dimension_evaluations(
        list(report.feedbacks if feedbacks is None else feedbacks)
    )
    coverage = aggregate_report_coverage(resolved_feedbacks)
    updates = {
        "feedbacks": resolved_feedbacks,
        "overall_score": coverage.overall_score,
        "overall_dimension_scores": coverage.overall_dimension_scores,
        "score_status": coverage.score_status,
        "score_reason_code": coverage.score_reason_code,
        "coverage_status": coverage.coverage_status,
        "evaluated_count": coverage.evaluated_count,
        "total_eligible_count": coverage.total_eligible_count,
        "evidence_count": coverage.evidence_count,
        "dimension_evaluations": dimension_evaluations(coverage),
        "question_evaluations": question_evaluations(resolved_feedbacks),
    }
    if report.report_schema_version == REPORT_SCHEMA_VERSION_V2:
        updates["coverage"] = ReportCoverageV2(
            status=coverage.coverage_status,
            evaluated_count=coverage.evaluated_count,
            total_eligible_count=coverage.total_eligible_count,
            evidence_count=coverage.evidence_count,
            per_dimension=dimension_evaluations(coverage),
        )
    if report_path is not None:
        updates["report_path"] = report_path
    return report.model_copy(update=updates)


def _mean(values: list[int]) -> int | None:
    if not values:
        return None
    return int(
        (Decimal(sum(values)) / Decimal(len(values))).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        )
    )


def _feedback_evidence_count(item: InterviewFeedback) -> int:
    if getattr(item, "evidence_count", 0):
        return item.evidence_count
    return sum(
        len([value for value in evidence.get("observed", []) if str(value).strip()])
        for evidence in getattr(item, "dimension_evidence", [])
        if isinstance(evidence, dict)
    )


def _dimension_evidence_count(item: InterviewFeedback, dimension: str) -> int:
    return sum(
        len([value for value in evidence.get("observed", []) if str(value).strip()])
        for evidence in getattr(item, "dimension_evidence", [])
        if isinstance(evidence, dict) and evidence.get("dimension") == dimension
    )


def _answer_state(item: InterviewFeedback) -> str:
    return str(getattr(item, "answer_state", "answered"))


def _evaluation_status(item: InterviewFeedback) -> str:
    status = getattr(item, "evaluation_status", None)
    if status:
        return str(status)
    return "evaluated" if getattr(item, "score", None) is not None else "insufficient_evidence"


def _applicable_dimensions(item: InterviewFeedback) -> list[str]:
    return list(getattr(item, "applicable_dimensions", []) or [])
