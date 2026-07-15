from dataclasses import dataclass
import logging
import re
from typing import Literal

from pydantic import BaseModel, Field

from app.services.report import DimensionScores

REPORT_SCORING_RUBRIC_VERSION = "stage40-rubric-v2"
logger = logging.getLogger(__name__)


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
    evidence_items = apply_rule_quality_signals(item, evidence_items)
    # Rubric v2 treats provider dimension labels as untrusted. It pools excerpts
    # and lets backend-owned applicability and signals assign dimension scores.
    # Preserving provider partitions requires a new rubric version and rescore.
    observed = [
        text
        for evidence in evidence_items
        for text in evidence.observed
        if text.strip()
    ]
    missing = [
        text
        for evidence in evidence_items
        for text in evidence.missing
        if text.strip()
    ]
    evidence_by_dimension = {
        dimension: DimensionEvidence(
            dimension=dimension,
            observed=observed,
            missing=missing,
            quality_signals=derive_quality_signals(item, dimension=dimension),
        )
        for dimension in applicable
        if observed
    }
    dimension_values = {
        dimension: score_dimension_evidence(evidence_by_dimension[dimension])
        if dimension in evidence_by_dimension
        else 0
        for dimension in DIMENSIONS
    }
    answer_cap = answer_quality_score_cap(item)
    dimension_values = {
        dimension: min(value, answer_cap)
        for dimension, value in dimension_values.items()
    }
    score = round(
        sum(
            dimension_values[dimension] * weights.get(dimension, 0)
            for dimension in applicable
        )
    )
    score = min(score, answer_cap)
    return RuleQuestionScore(
        score=score,
        dimension_scores=DimensionScores(**dimension_values),
        applicable_dimensions=applicable,
    )


def apply_rule_quality_signals(
    item: dict,
    evidence_items: list[DimensionEvidence],
) -> list[DimensionEvidence]:
    return [
        evidence.model_copy(
            update={
                "quality_signals": derive_quality_signals(
                    item,
                    dimension=evidence.dimension,
                )
                if _has_observed_evidence(evidence)
                else [],
            }
        )
        for evidence in evidence_items
    ]


def derive_quality_signals(
    item: dict,
    *,
    dimension: DimensionName,
) -> list[QualitySignal]:
    answer = _candidate_answer_text(item).lower()
    meaningful = re.sub(r"[\W_]+", "", answer, flags=re.UNICODE)
    detected: list[QualitySignal] = []
    if len(meaningful) >= 20:
        detected.append("concept")
    if _contains_any(answer, ("step", "first", "then", "next", "finally", "1.", "2.")):
        detected.append("concrete_steps")
    if _contains_any(answer, ("tradeoff", "trade-off", "however", "but", "instead", "cost")):
        detected.append("tradeoff")
    if _contains_any(answer, ("risk", "failure", "fail", "race", "inconsistent", "timeout", "loss")):
        detected.append("risk")
    if _contains_any(answer, ("ttl", "fallback", "retry", "rollback", "compensat", "degrad")):
        detected.append("fallback")
    if _contains_any(answer, ("p95", "p99", "qps", "latency", "throughput", "error rate", "lag")):
        detected.append("metric")
    if _contains_any(answer, ("production", "monitor", "alert", "on-call", "runbook", "canary")):
        detected.append("production")
    if _contains_any(answer, ("api", "sql", "explain", "redis", "binlog", "mq", "http", "endpoint", "offset")):
        detected.append("code_or_api")
    if len(meaningful) >= 30 and _contains_any(answer, (",", ".", ";", ":")):
        detected.append("clarity")

    allowed_by_dimension: dict[DimensionName, set[QualitySignal]] = {
        "breadth": {"concept", "tradeoff", "risk", "code_or_api"},
        "depth": {"concept", "concrete_steps", "tradeoff", "risk", "fallback", "metric", "code_or_api", "clarity"},
        "architecture": {"concept", "concrete_steps", "tradeoff", "risk", "fallback", "metric", "production", "clarity"},
        "engineering": {"concept", "concrete_steps", "risk", "fallback", "metric", "production", "code_or_api", "clarity"},
        "communication": {"clarity"},
    }
    allowed = allowed_by_dimension[dimension]
    return [signal for signal in detected if signal in allowed]


def score_question_without_evidence(item: dict) -> RuleQuestionScore:
    applicable = applicable_dimensions_for_item(item)
    return RuleQuestionScore(
        score=0,
        dimension_scores=DimensionScores(
            breadth=0,
            depth=0,
            architecture=0,
            engineering=0,
            communication=0,
        ),
        applicable_dimensions=applicable,
    )


def answer_quality_score_cap(item: dict) -> int:
    if str(item.get("answer_state") or "answered").strip() != "answered":
        return 0
    if not _has_answer_payload(item):
        logger.warning(
            "score item has no answer payload; leaving score cap unrestricted",
            extra={"question_id": item.get("question_id")},
        )
        return 100

    answer = _candidate_answer_text(item)
    meaningful = re.sub(r"[\W_]+", "", answer, flags=re.UNICODE)
    if len(meaningful) < 8:
        return 0

    normalized = answer.strip().lower()
    compact = re.sub(r"\s+", "", normalized)
    if compact in _LOW_INFORMATION_ANSWERS:
        return 0
    if re.fullmatch(r"[\d\W_]+", answer):
        return 0
    if len(meaningful) < 20:
        return 20
    if _looks_like_repeated_placeholder(meaningful):
        return 20
    if _looks_like_low_information_answer(compact):
        return 40
    if _contains_any(normalized, ("does not answer", "not answering", "off topic", "\u6ca1\u6709\u56de\u7b54", "\u672a\u56de\u7b54", "\u6ca1\u6709\u590d\u76d8", "\u4e0e\u95ee\u9898\u65e0\u5173")):
        return 0
    if _contains_unsafe_absolute_claim(answer.lower()):
        return 35
    return 100


def _contains_unsafe_absolute_claim(answer: str) -> bool:
    absolute_terms = (
        "always",
        "never",
        "absolutely",
        "guaranteed",
        "zero failure",
        "\u5929\u7136\u5f3a\u4e00\u81f4",
        "\u7edd\u5bf9\u4e0d\u4f1a",
        "\u4e00\u5b9a\u4f7f\u7528",
        "\u4fdd\u8bc1\u96f6\u6545\u969c",
        "\u5b8c\u5168\u6b63\u5e38",
    )
    dismissive_terms = (
        "no need",
        "do not need",
        "don't need",
        "unused",
        "\u4e0d\u7528\u5904\u7406",
        "\u65e0\u9700",
        "\u4e0d\u9700\u8981",
        "\u4e0d\u7528\u5206\u6790",
        "\u4e0d\u4f1a\u4e22\u5931",
    )
    return _contains_any(answer, absolute_terms) and _contains_any(answer, dismissive_terms)


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
            if _dimension_applies(feedback, dimension)
            else 0
            for feedback in feedbacks
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


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


_LOW_INFORMATION_ANSWERS = {
    "1",
    "2",
    "3",
    "test",
    "asdf",
    "n/a",
    "na",
    "none",
    "no",
    "不知道",
    "不会",
    "不清楚",
    "随便",
    "没有",
    "无",
}


def _candidate_answer_text(item: dict) -> str:
    messages = item.get("messages") or []
    parts = [
        str(message.get("content") or "").strip()
        for message in messages
        if isinstance(message, dict)
        and message.get("role") == "candidate"
        and str(message.get("content") or "").strip()
    ]
    if parts:
        return " ".join(parts)
    return str(item.get("user_answer") or item.get("answer") or "").strip()


def _has_answer_payload(item: dict) -> bool:
    return "messages" in item or "user_answer" in item or "answer" in item


def _looks_like_repeated_placeholder(meaningful: str) -> bool:
    lowered = meaningful.lower()
    return len(set(lowered)) <= 2 and len(lowered) < 30


def _looks_like_low_information_answer(compact: str) -> bool:
    low_information_phrases = (
        "idontknow",
        "not sure",
        "notreallysure",
        "cannotanswer",
        "noidea",
        "whatever",
        "随便说",
        "我不知道",
        "我不会",
        "不太清楚",
    )
    return any(phrase in compact for phrase in low_information_phrases)
