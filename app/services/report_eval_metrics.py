import re
import math
from collections import defaultdict
from itertools import combinations
from typing import Literal
from pydantic import BaseModel, Field

from app.services.interview_quality_gate import (
    GateConfig,
    MetricEvaluation,
    evaluate_metric,
    load_gate_config,
)

QualityLevel = Literal["strong", "medium", "incorrect", "off_topic", "empty"]
QUALITY_ORDER = {"strong": 4, "medium": 3, "incorrect": 2, "off_topic": 1, "empty": 0}

class AttemptResult(BaseModel):
    schema_version: str = "report-quality-evaluation-attempt-v1"
    case_id: str
    group_id: str
    quality_level: QualityLevel
    run_number: int = Field(ge=1)
    score: float | None = Field(default=None, ge=0, le=100)
    expected_score_range: tuple[int, int] = (0, 100)
    language: str = "unknown"
    question_type: str = "unknown"
    answer: str
    observed: list[str] = Field(default_factory=list)
    required_observations: list[str] = Field(default_factory=list)
    forbidden_claims: list[str] = Field(default_factory=list)
    applicable_dimensions: list[str] = Field(default_factory=list)
    expected_applicable_dimensions: list[str] = Field(default_factory=list)
    fallback: bool = False
    provider_owned: dict = Field(default_factory=dict)
    backend_owned: dict = Field(default_factory=dict)
    provider_forbidden_claim: bool = False
    backend_guidance_forbidden_claim: bool = False
    provider_forbidden_claims_detected: list[str] = Field(default_factory=list)
    backend_guidance_forbidden_claims_detected: list[str] = Field(
        default_factory=list
    )
    output_text: str = ""

class EvaluationMetrics(BaseModel):
    passed: bool
    decision: Literal["PASS", "FAIL", "INSUFFICIENT_SAMPLE"]
    ranking_accuracy: float
    evidence_grounding_rate: float
    max_score_delta: float
    fallback_rate: float
    expected_range_attempt_hit_rate: float
    strong_attempt_hit_rate: float
    interval_outside_mae: float
    expert_score_spearman: float
    strata: dict[str, dict]
    out_of_range_attempts: list[dict]
    strong_below_lower_bound: list[dict]
    gate_results: list[MetricEvaluation]
    completed_attempt_count: int
    expected_attempt_count: int
    failed_gates: list[str] = Field(default_factory=list)
    blocking_failures: list[dict] = Field(default_factory=list)
    provider_forbidden_claim_count: int = Field(default=0, ge=0)
    backend_guidance_forbidden_claim_count: int = Field(default=0, ge=0)

def normalize_text(value: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", value.lower())


def ngram_coverage(value: str, source: str, *, size: int = 2) -> float:
    value_text = normalize_text(value)
    source_text = normalize_text(source)
    if not value_text:
        return 0.0
    if value_text in source_text:
        return 1.0
    if len(value_text) < size:
        return float(value_text in source_text)
    value_grams = {value_text[index : index + size] for index in range(len(value_text) - size + 1)}
    source_grams = {source_text[index : index + size] for index in range(len(source_text) - size + 1)}
    return len(value_grams & source_grams) / len(value_grams)

def calculate_metrics(
    attempts,
    *,
    expected_attempt_count: int,
    gate_config: GateConfig | None = None,
) -> EvaluationMetrics:
    config = gate_config or load_gate_config()
    items = [a if isinstance(a, AttemptResult) else AttemptResult.model_validate(a) for a in attempts]
    ranking = _ranking(items)
    grounding = _grounding(
        items,
        ngram_min_coverage=config.algorithm_parameters[
            "evidence_ngram_min_coverage"
        ],
    )
    delta = _delta(items)
    fallback = sum(a.fallback for a in items) / len(items) if items else 0.0
    completeness = len(items) / expected_attempt_count if expected_attempt_count else 0.0
    range_hit = _interval_hit_rate(items)
    strong_items = [item for item in items if item.quality_level == "strong"]
    strong_hit = _interval_hit_rate(strong_items)
    outside_mae = _interval_outside_mae(items)
    spearman = _spearman(items)
    strata = _strata(items, config=config)
    gate_inputs = (
        ("ranking_accuracy", "report_scoring.pairwise_ranking_accuracy", ranking, len(items)),
        ("evidence_grounding_rate", "report_scoring.evidence_grounding_rate", grounding, len(items)),
        ("score_stability", "report_scoring.provider_repeat_max_delta", delta, len(items)),
        ("fallback_rate", "report_scoring.fallback_rate", fallback, len(items)),
        ("attempt_completeness", "report_scoring.attempt_completeness_rate", completeness, expected_attempt_count),
        ("expected_range_attempt_hit_rate", "report_scoring.expected_range_attempt_hit_rate", range_hit, len(items)),
        ("strong_attempt_hit_rate", "report_scoring.strong_attempt_hit_rate", strong_hit, len(strong_items)),
        ("expert_score_spearman", "report_scoring.expert_score_spearman", spearman, len(items)),
        ("interval_outside_mae", "report_scoring.interval_outside_mae", outside_mae, len(items)),
    )
    gate_results = [
        evaluate_metric(
            config,
            metric_key,
            actual=actual,
            sample_size=sample_size,
        )
        for _, metric_key, actual, sample_size in gate_inputs
    ]
    gate_results.extend(
        MetricEvaluation.model_validate(result["gate"])
        for result in strata.values()
    )
    failed = [
        name
        for (name, _, _, _), result in zip(gate_inputs, gate_results)
        if result.status != "PASS"
    ]
    failed.extend(
        f"stratum:{name}"
        for name, result in strata.items()
        if result["gate"]["status"] != "PASS"
    )
    blocking = _blocking(items, expected_attempt_count)
    provider_forbidden_claim_count = sum(
        failure["type"] == "provider_forbidden_claim" for failure in blocking
    )
    backend_guidance_forbidden_claim_count = sum(
        failure["type"] == "backend_guidance_forbidden_claim"
        for failure in blocking
    )
    out_of_range = [_range_failure(item) for item in items if not _interval_hit(item)]
    strong_below = [
        _range_failure(item)
        for item in strong_items
        if item.score is None or item.score < item.expected_score_range[0]
    ]
    insufficient = any(
        result.status in {"INSUFFICIENT_SAMPLE", "INSUFFICIENT_BASELINE"}
        for result in gate_results
    )
    passed = not failed and not blocking
    decision = "PASS" if passed else "INSUFFICIENT_SAMPLE" if insufficient else "FAIL"
    return EvaluationMetrics(
        passed=passed,
        decision=decision,
        ranking_accuracy=ranking,
        evidence_grounding_rate=grounding,
        max_score_delta=delta,
        fallback_rate=fallback,
        expected_range_attempt_hit_rate=range_hit,
        strong_attempt_hit_rate=strong_hit,
        interval_outside_mae=outside_mae,
        expert_score_spearman=spearman,
        strata=strata,
        out_of_range_attempts=out_of_range,
        strong_below_lower_bound=strong_below,
        gate_results=gate_results,
        completed_attempt_count=len(items),
        expected_attempt_count=expected_attempt_count,
        failed_gates=failed,
        blocking_failures=blocking,
        provider_forbidden_claim_count=provider_forbidden_claim_count,
        backend_guidance_forbidden_claim_count=(
            backend_guidance_forbidden_claim_count
        ),
    )

def _ranking(items):
    scores, groups = defaultdict(list), defaultdict(list)
    for a in items:
        if a.score is not None:
            scores[(a.group_id, a.case_id, a.quality_level)].append(a.score)
    for (group, _, quality), values in scores.items(): groups[group].append((quality, sum(values) / len(values)))
    passed = total = 0
    for cases in groups.values():
        for left, right in combinations(cases, 2):
            if QUALITY_ORDER[left[0]] == QUALITY_ORDER[right[0]]: continue
            high, low = (left, right) if QUALITY_ORDER[left[0]] > QUALITY_ORDER[right[0]] else (right, left)
            total += 1; passed += high[1] > low[1]
    return passed / total if total else 1.0

def _grounding(items, *, ngram_min_coverage: float):
    grounded = total = 0
    for a in items:
        if not a.observed:
            total += 1; grounded += a.quality_level in {"empty", "off_topic"}; continue
        answer = normalize_text(a.answer); terms = [normalize_text(t) for t in a.required_observations if normalize_text(t)]
        for evidence in a.observed:
            total += 1; value = normalize_text(evidence)
            grounded += (
                ngram_coverage(evidence, a.answer) >= ngram_min_coverage
                or any(t in value and t in answer for t in terms)
            )
    return grounded / total if total else 1.0


def _delta(items):
    scores = defaultdict(list)
    for a in items:
        if not a.fallback and a.score is not None:
            scores[a.case_id].append(a.score)
    return max((max(v) - min(v) for v in scores.values() if len(v) >= 2), default=0.0)

def _blocking(items, expected):
    failures = []
    if len(items) != expected: failures.append({"type": "incomplete_attempts", "completed": len(items), "expected": expected})
    for a in items:
        provider_claims, backend_claims = classify_forbidden_claims(a)
        for claim in provider_claims:
            failures.append({"type": "provider_forbidden_claim", "case_id": a.case_id, "run_number": a.run_number, "claim": claim, "owner": "provider"})
        for claim in backend_claims:
            failures.append({"type": "backend_guidance_forbidden_claim", "case_id": a.case_id, "run_number": a.run_number, "claim": claim, "owner": "backend_answer_guidance"})
        if set(a.applicable_dimensions) != set(a.expected_applicable_dimensions): failures.append({"type": "dimension_mismatch", "case_id": a.case_id, "run_number": a.run_number})
        if a.quality_level == "empty" and a.score not in {None, 0}: failures.append({"type": "empty_non_zero", "case_id": a.case_id, "run_number": a.run_number, "score": a.score})
        if a.quality_level != "empty" and a.score is None and not a.fallback: failures.append({"type": "unexpected_unscored", "case_id": a.case_id, "run_number": a.run_number})
    return failures


def classify_forbidden_claims(
    attempt: AttemptResult | dict,
) -> tuple[list[str], list[str]]:
    item = (
        attempt
        if isinstance(attempt, AttemptResult)
        else AttemptResult.model_validate(attempt)
    )
    provider_owned = item.provider_owned or {
        "observed": item.observed,
        # Legacy output_text was the Provider-facing surface. New live
        # attempts never mix Backend guidance into this compatibility field.
        "rationale": item.output_text,
    }
    backend_owned = item.backend_owned or {}
    provider_text = normalize_text(
        " ".join(_owned_text_values(provider_owned, include_numeric=False))
    )
    backend_guidance = normalize_text(
        str(backend_owned.get("answer_guidance") or "")
    )
    answer = normalize_text(item.answer)
    provider_claims: list[str] = []
    backend_claims: list[str] = []
    for claim in item.forbidden_claims:
        normalized_claim = normalize_text(claim)
        if not normalized_claim or normalized_claim in answer:
            continue
        if normalized_claim in provider_text:
            provider_claims.append(claim)
        if normalized_claim in backend_guidance:
            backend_claims.append(claim)
    return provider_claims, backend_claims


def _owned_text_values(value: object, *, include_numeric: bool) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [
            text
            for item in value
            for text in _owned_text_values(item, include_numeric=include_numeric)
        ]
    if isinstance(value, dict):
        return [
            text
            for item in value.values()
            for text in _owned_text_values(item, include_numeric=include_numeric)
        ]
    if include_numeric and isinstance(value, (int, float)) and not isinstance(value, bool):
        return [str(value)]
    return []


def _interval_hit(item: AttemptResult) -> bool:
    if item.quality_level == "empty":
        if item.score is None:
            return True
        low, high = item.expected_score_range
        return low <= item.score <= high
    if item.score is None:
        return False
    low, high = item.expected_score_range
    return low <= item.score <= high


def _interval_hit_rate(items: list[AttemptResult]) -> float:
    return sum(_interval_hit(item) for item in items) / len(items) if items else 0.0


def _interval_outside_mae(items: list[AttemptResult]) -> float:
    if not items:
        return 0.0
    distances = []
    for item in items:
        if item.quality_level == "empty" and item.score is None:
            distances.append(0.0)
            continue
        if item.score is None:
            distances.append(100.0)
            continue
        low, high = item.expected_score_range
        distances.append(max(low - item.score, 0, item.score - high))
    return sum(distances) / len(distances)


def _average_ranks(values: list[float]) -> list[float]:
    ordered = sorted(enumerate(values), key=lambda pair: pair[1])
    ranks = [0.0] * len(values)
    index = 0
    while index < len(ordered):
        end = index + 1
        while end < len(ordered) and ordered[end][1] == ordered[index][1]:
            end += 1
        rank = (index + 1 + end) / 2
        for original_index, _ in ordered[index:end]:
            ranks[original_index] = rank
        index = end
    return ranks


def _spearman(items: list[AttemptResult]) -> float:
    scored = [item for item in items if item.score is not None]
    if len(scored) < 2:
        return 0.0
    expected = [sum(item.expected_score_range) / 2 for item in scored]
    actual = [float(item.score) for item in scored]
    expected_ranks = _average_ranks(expected)
    actual_ranks = _average_ranks(actual)
    expected_mean = sum(expected_ranks) / len(expected_ranks)
    actual_mean = sum(actual_ranks) / len(actual_ranks)
    numerator = sum(
        (left - expected_mean) * (right - actual_mean)
        for left, right in zip(expected_ranks, actual_ranks)
    )
    denominator = math.sqrt(
        sum((value - expected_mean) ** 2 for value in expected_ranks)
        * sum((value - actual_mean) ** 2 for value in actual_ranks)
    )
    return numerator / denominator if denominator else 0.0


def _strata(items: list[AttemptResult], *, config: GateConfig) -> dict[str, dict]:
    groups: dict[str, list[AttemptResult]] = {"overall": list(items)}
    for field_name in ("quality_level", "language", "question_type"):
        for item in items:
            value = str(getattr(item, field_name))
            groups.setdefault(f"{field_name}:{value}", []).append(item)
    results: dict[str, dict] = {}
    for name, members in sorted(groups.items()):
        rate = _interval_hit_rate(members)
        gate = evaluate_metric(
            config,
            "report_scoring.blocking_stratum_hit_rate",
            actual=rate,
            sample_size=len(members),
        )
        results[name] = {
            "attempt_count": len(members),
            "hit_count": sum(_interval_hit(item) for item in members),
            "hit_rate": rate,
            "gate": gate.model_dump(mode="json"),
        }
    return results


def _range_failure(item: AttemptResult) -> dict:
    return {
        "case_id": item.case_id,
        "run_number": item.run_number,
        "quality_level": item.quality_level,
        "language": item.language,
        "question_type": item.question_type,
        "score": item.score,
        "expected_score_range": list(item.expected_score_range),
    }
