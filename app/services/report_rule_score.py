from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field

from app.services.report import DimensionScores


DimensionName = Literal[
    "breadth",
    "depth",
    "architecture",
    "engineering",
    "communication",
]

QualitySignal = Literal[
    "concept",
    "concrete_steps",
    "tradeoff",
    "risk",
    "fallback",
    "metric",
    "production",
    "code_or_api",
    "clarity",
]

DIMENSIONS: tuple[DimensionName, ...] = (
    "breadth",
    "depth",
    "architecture",
    "engineering",
    "communication",
)

QUESTION_KIND_DIMENSIONS: dict[str, list[DimensionName]] = {
    "project": ["engineering", "depth", "communication"],
    "technical": ["depth", "engineering", "breadth", "communication"],
    "system-design": ["architecture", "engineering", "depth", "communication"],
    "behavioral": ["communication", "engineering"],
}

QUESTION_KIND_WEIGHTS: dict[str, dict[DimensionName, float]] = {
    "project": {"engineering": 0.45, "depth": 0.35, "communication": 0.20},
    "technical": {
        "depth": 0.45,
        "engineering": 0.35,
        "breadth": 0.10,
        "communication": 0.10,
    },
    "system-design": {
        "architecture": 0.45,
        "engineering": 0.25,
        "depth": 0.20,
        "communication": 0.10,
    },
    "behavioral": {"communication": 0.60, "engineering": 0.40},
}

FOCUS_KEYWORDS: list[tuple[tuple[str, ...], list[DimensionName]]] = [
    (
        (
            "系统设计",
            "架构设计",
            "高并发",
            "分布式",
            "容量估算",
            "扩展性",
            "system design",
            "architecture",
        ),
        ["architecture", "engineering", "depth", "communication"],
    ),
    (
        ("技术", "原理", "源码", "Redis", "Kafka", "MySQL", "一致性", "缓存"),
        ["depth", "engineering", "breadth", "communication"],
    ),
    (
        ("项目", "工程", "实践", "落地", "排查", "优化"),
        ["engineering", "depth", "communication"],
    ),
    (("表达", "沟通", "协作", "复盘", "冲突"), ["communication", "engineering"]),
]

SIGNAL_POINTS: dict[QualitySignal, int] = {
    "concrete_steps": 15,
    "tradeoff": 10,
    "risk": 10,
    "fallback": 10,
    "metric": 10,
    "production": 10,
    "code_or_api": 10,
    "clarity": 5,
}


class DimensionEvidence(BaseModel):
    dimension: DimensionName
    observed: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
    quality_signals: list[QualitySignal] = Field(default_factory=list)


@dataclass(frozen=True)
class RuleQuestionScore:
    score: int
    dimension_scores: DimensionScores
    applicable_dimensions: list[DimensionName]


def applicable_dimensions_for_item(item: dict) -> list[DimensionName]:
    kind = str(item.get("question_kind") or item.get("kind") or "").strip()
    if kind in QUESTION_KIND_DIMENSIONS:
        return list(QUESTION_KIND_DIMENSIONS[kind])

    text = " ".join(
        str(item.get(key) or "")
        for key in ("focus", "question_text", "question", "prompt")
    )
    for keywords, dimensions in FOCUS_KEYWORDS:
        if any(keyword.lower() in text.lower() for keyword in keywords):
            return list(dimensions)
    return ["depth", "engineering", "communication"]


def score_dimension_evidence(evidence: DimensionEvidence) -> int:
    if not _has_observed_evidence(evidence):
        return 0

    signals = list(dict.fromkeys(evidence.quality_signals))
    if not signals:
        return 40

    score = 40
    for signal in signals:
        if signal == "concept":
            continue
        score += SIGNAL_POINTS[signal]

    if evidence.missing and score > 85:
        score = 85
    return max(0, min(score, 95))


def score_question_from_evidence(
    item: dict,
    evidence_items: list[DimensionEvidence],
) -> RuleQuestionScore:
    applicable = applicable_dimensions_for_item(item)
    weights = weights_for_item(item, applicable)
    evidence_by_dimension = {
        evidence.dimension: evidence
        for evidence in evidence_items
        if evidence.dimension in applicable
    }
    dimension_values = {
        dimension: score_dimension_evidence(evidence_by_dimension[dimension])
        if dimension in evidence_by_dimension
        else 0
        for dimension in DIMENSIONS
    }
    score = round(
        sum(
            dimension_values[dimension] * weights.get(dimension, 0)
            for dimension in applicable
        )
    )
    return RuleQuestionScore(
        score=score,
        dimension_scores=DimensionScores(**dimension_values),
        applicable_dimensions=applicable,
    )


def weights_for_item(
    item: dict,
    applicable: list[DimensionName],
) -> dict[DimensionName, float]:
    kind = str(item.get("question_kind") or item.get("kind") or "").strip()
    weights = QUESTION_KIND_WEIGHTS.get(kind)
    if weights is not None:
        return weights
    if not applicable:
        return {}
    equal_weight = 1 / len(applicable)
    return {dimension: equal_weight for dimension in applicable}


def aggregate_feedback_scores(feedbacks) -> tuple[int, DimensionScores]:
    feedbacks = list(feedbacks)
    if not feedbacks:
        return 0, DimensionScores(
            breadth=0,
            depth=0,
            architecture=0,
            engineering=0,
            communication=0,
        )

    overall_score = round(sum(feedback.score for feedback in feedbacks) / len(feedbacks))
    dimension_values = {}
    for dimension in DIMENSIONS:
        values = [
            getattr(feedback.dimension_scores, dimension)
            for feedback in feedbacks
            if _dimension_applies(feedback, dimension)
        ]
        dimension_values[dimension] = round(sum(values) / len(values)) if values else 0
    return overall_score, DimensionScores(**dimension_values)


def _dimension_applies(feedback, dimension: str) -> bool:
    applicable = list(getattr(feedback, "applicable_dimensions", []) or [])
    if not applicable:
        return True
    return dimension in applicable


def _has_observed_evidence(evidence: DimensionEvidence) -> bool:
    return any(text.strip() for text in evidence.observed)
