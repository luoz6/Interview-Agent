import re
from collections import defaultdict
from itertools import combinations
from typing import Literal
from pydantic import BaseModel, Field

QualityLevel = Literal["strong", "medium", "incorrect", "off_topic", "empty"]
QUALITY_ORDER = {"strong": 4, "medium": 3, "incorrect": 2, "off_topic": 1, "empty": 0}

class AttemptResult(BaseModel):
    case_id: str
    group_id: str
    quality_level: QualityLevel
    run_number: int = Field(ge=1)
    score: float = Field(ge=0, le=100)
    answer: str
    observed: list[str] = Field(default_factory=list)
    required_observations: list[str] = Field(default_factory=list)
    forbidden_claims: list[str] = Field(default_factory=list)
    applicable_dimensions: list[str] = Field(default_factory=list)
    expected_applicable_dimensions: list[str] = Field(default_factory=list)
    fallback: bool = False
    output_text: str = ""

class EvaluationMetrics(BaseModel):
    passed: bool
    ranking_accuracy: float
    evidence_grounding_rate: float
    max_score_delta: float
    fallback_rate: float
    completed_attempt_count: int
    expected_attempt_count: int
    failed_gates: list[str] = Field(default_factory=list)
    blocking_failures: list[dict] = Field(default_factory=list)

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

def calculate_metrics(attempts, *, expected_attempt_count: int) -> EvaluationMetrics:
    items = [a if isinstance(a, AttemptResult) else AttemptResult.model_validate(a) for a in attempts]
    ranking, grounding, delta = _ranking(items), _grounding(items), _delta(items)
    fallback = sum(a.fallback for a in items) / len(items) if items else 0.0
    failed = [name for name, bad in (("ranking_accuracy", ranking < .85), ("evidence_grounding_rate", grounding < .9), ("score_stability", delta > 8), ("fallback_rate", fallback > .05), ("attempt_completeness", len(items) != expected_attempt_count)) if bad]
    blocking = _blocking(items, expected_attempt_count)
    return EvaluationMetrics(passed=not failed and not blocking, ranking_accuracy=ranking, evidence_grounding_rate=grounding, max_score_delta=delta, fallback_rate=fallback, completed_attempt_count=len(items), expected_attempt_count=expected_attempt_count, failed_gates=failed, blocking_failures=blocking)

def _ranking(items):
    scores, groups = defaultdict(list), defaultdict(list)
    for a in items: scores[(a.group_id, a.case_id, a.quality_level)].append(a.score)
    for (group, _, quality), values in scores.items(): groups[group].append((quality, sum(values) / len(values)))
    passed = total = 0
    for cases in groups.values():
        for left, right in combinations(cases, 2):
            if QUALITY_ORDER[left[0]] == QUALITY_ORDER[right[0]]: continue
            high, low = (left, right) if QUALITY_ORDER[left[0]] > QUALITY_ORDER[right[0]] else (right, left)
            total += 1; passed += high[1] > low[1]
    return passed / total if total else 1.0

def _grounding(items):
    grounded = total = 0
    for a in items:
        if not a.observed:
            total += 1; grounded += a.quality_level in {"empty", "off_topic"}; continue
        answer = normalize_text(a.answer); terms = [normalize_text(t) for t in a.required_observations if normalize_text(t)]
        for evidence in a.observed:
            total += 1; value = normalize_text(evidence)
            grounded += (
                ngram_coverage(evidence, a.answer) >= 0.75
                or any(t in value and t in answer for t in terms)
            )
    return grounded / total if total else 1.0

def _delta(items):
    scores = defaultdict(list)
    for a in items:
        if not a.fallback: scores[a.case_id].append(a.score)
    return max((max(v) - min(v) for v in scores.values() if len(v) >= 2), default=0.0)

def _blocking(items, expected):
    failures = []
    if len(items) != expected: failures.append({"type": "incomplete_attempts", "completed": len(items), "expected": expected})
    for a in items:
        output = normalize_text(" ".join([*a.observed, a.output_text]))
        answer = normalize_text(a.answer)
        for claim in a.forbidden_claims:
            normalized_claim = normalize_text(claim)
            if normalized_claim and normalized_claim in output and normalized_claim not in answer:
                failures.append({"type": "forbidden_claim", "case_id": a.case_id, "run_number": a.run_number, "claim": claim})
        if set(a.applicable_dimensions) != set(a.expected_applicable_dimensions): failures.append({"type": "dimension_mismatch", "case_id": a.case_id, "run_number": a.run_number})
        if a.quality_level == "empty" and a.score != 0: failures.append({"type": "empty_non_zero", "case_id": a.case_id, "run_number": a.run_number, "score": a.score})
    return failures
