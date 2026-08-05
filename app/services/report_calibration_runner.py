from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.services.report_calibration_dataset import CalibrationCase, CalibrationDataset
from app.services.report_eval_metrics import AttemptResult, EvaluationMetrics, calculate_metrics
from app.services.report_rule_score import (
    REPORT_SCORING_RUBRIC_SHA256,
    REPORT_SCORING_RUBRIC_VERSION,
    DimensionEvidence,
    applicable_dimensions_for_item,
    score_question_from_evidence,
)


class CalibrationRunResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    partition: Literal["dev", "blind", "all"]
    rubric_version: str
    rubric_sha256: str
    dataset_review_status: str
    provider_invocations: Literal[0] = 0
    metrics: EvaluationMetrics
    error_categories: dict[str, int]
    case_failures: list[dict]


def evaluate_calibration_dataset(
    dataset: CalibrationDataset,
    *,
    partition: Literal["dev", "blind", "all"] = "dev",
    allow_unreviewed_dev: bool = False,
) -> CalibrationRunResult:
    if partition in {"blind", "all"}:
        dataset.require_gate_eligible()
    elif not dataset.gate_eligible and not allow_unreviewed_dev:
        dataset.require_gate_eligible()
    cases = [
        case
        for case in dataset.cases
        if partition == "all" or case.partition == partition
    ]
    attempts = [_evaluate_annotated_case(case) for case in cases]
    metrics = calculate_metrics(attempts, expected_attempt_count=len(cases))
    failures = [
        _classify_failure(case, attempt)
        for case, attempt in zip(cases, attempts)
        if not _inside_expected_range(case, attempt.score)
    ]
    categories: dict[str, int] = {}
    for failure in failures:
        category = failure["category"]
        categories[category] = categories.get(category, 0) + 1
    return CalibrationRunResult(
        partition=partition,
        rubric_version=REPORT_SCORING_RUBRIC_VERSION,
        rubric_sha256=REPORT_SCORING_RUBRIC_SHA256,
        dataset_review_status=dataset.review_status,
        metrics=metrics,
        error_categories=categories,
        case_failures=failures if partition == "dev" else [],
    )


def _evaluate_annotated_case(case: CalibrationCase) -> AttemptResult:
    kind_map = {
        "technical": "technical",
        "system_design": "system-design",
        "project_review": "project",
        "behavioral": "behavioral",
    }
    item = {
        "question_id": case.case_id,
        "question_kind": kind_map[case.question_type],
        "question_text": case.question,
        "focus": case.question,
        "answer_state": "answered",
        "messages": [{"role": "candidate", "content": case.answer}],
    }
    applicable = applicable_dimensions_for_item(item)
    evidence = [
        DimensionEvidence(
            dimension=applicable[0],
            observed=list(case.required_evidence),
            missing=list(case.required_missing_points),
        )
    ]
    score = score_question_from_evidence(item, evidence)
    return AttemptResult(
        case_id=case.case_id,
        group_id=case.group_id,
        quality_level=case.quality_label,
        run_number=1,
        score=score.score,
        expected_score_range=case.expected_score_range,
        language=case.language,
        question_type=case.question_type,
        answer=case.answer,
        observed=list(case.required_evidence),
        required_observations=list(case.required_evidence),
        forbidden_claims=list(case.forbidden_claims),
        applicable_dimensions=score.applicable_dimensions,
        expected_applicable_dimensions=applicable,
        fallback=False,
    )


def _inside_expected_range(case: CalibrationCase, score: float | None) -> bool:
    if score is None:
        return case.quality_label == "empty"
    low, high = case.expected_score_range
    return low <= score <= high


def _classify_failure(case: CalibrationCase, attempt: AttemptResult) -> dict:
    score = attempt.score
    low, high = case.expected_score_range
    if case.quality_label == "strong" and (score is None or score < low):
        category = "strong_underestimate"
    elif case.quality_label == "medium" and score is not None and score > high:
        category = "medium_overestimate"
    elif case.quality_label == "incorrect" and score is not None and score > high:
        category = "technical_error_not_capped"
    elif any(tag == "negation_context" for tag in case.error_tags):
        category = "negation_false_reward"
    elif score is None and case.quality_label != "empty":
        category = "evidence_extraction_failure"
    else:
        category = "interval_miss"
    return {
        "case_id": case.case_id,
        "category": category,
        "quality_label": case.quality_label,
        "language": case.language,
        "question_type": case.question_type,
        "score": score,
        "expected_score_range": [low, high],
    }
