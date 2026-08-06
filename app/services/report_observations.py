from __future__ import annotations

import hashlib
import re
from typing import Iterable, Mapping

from app.services.report import (
    InterviewFeedback,
    REPORT_DIMENSIONS,
    ReportEvidenceRefV2,
    ReportObservationV2,
    ScoreEvaluation,
)


SIGNAL_TOPICS = {
    "concept": "technical_foundation",
    "concrete_steps": "structured_execution",
    "tradeoff": "tradeoff_analysis",
    "risk": "risk_identification",
    "fallback": "recovery_strategy",
    "metric": "measurable_outcomes",
    "production": "production_operations",
    "code_or_api": "technical_specificity",
    "clarity": "communication_clarity",
}

TOPIC_TERMS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "measurable_outcomes",
        (
            "metric",
            "qps",
            "latency",
            "throughput",
            "error rate",
            "指标",
            "延迟",
            "吞吐",
            "错误率",
            "命中率",
        ),
    ),
    (
        "tradeoff_analysis",
        ("tradeoff", "trade-off", "cost", "取舍", "权衡", "代价", "成本"),
    ),
    (
        "recovery_strategy",
        (
            "fallback",
            "retry",
            "rollback",
            "recover",
            "兜底",
            "重试",
            "回滚",
            "恢复",
            "降级",
        ),
    ),
    (
        "production_operations",
        (
            "production",
            "monitor",
            "alert",
            "runbook",
            "生产",
            "监控",
            "告警",
            "预案",
            "灰度",
        ),
    ),
    (
        "risk_identification",
        ("risk", "failure", "race", "风险", "故障", "竞态", "不一致"),
    ),
    (
        "architecture_design",
        ("architecture", "scalab", "架构", "扩展", "容量"),
    ),
    (
        "communication_clarity",
        ("clarity", "structure", "清晰", "表达", "结构"),
    ),
    (
        "technical_specificity",
        ("api", "sql", "redis", "kafka", "mysql", "接口", "索引", "事务"),
    ),
)

MISSING_CODE_TOPICS = {
    "tradeoff_gap": "tradeoff_analysis",
    "metric_gap": "measurable_outcomes",
    "production_gap": "production_operations",
    "recovery_gap": "recovery_strategy",
}

SEVERE_RISK_TERMS = (
    "critical",
    "security",
    "data loss",
    "corruption",
    "unsafe",
    "incorrect",
    "安全漏洞",
    "数据丢失",
    "数据损坏",
    "错误结论",
    "绝对化",
)

DEFAULT_ROLE_RELEVANCE = {
    "breadth": "medium",
    "depth": "high",
    "architecture": "high",
    "engineering": "high",
    "communication": "medium",
}

TYPE_ORDER = {
    "risk": 0,
    "gap": 1,
    "strength": 2,
    "limitation": 3,
}
SEVERITY_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}


def aggregate_report_observations(
    *,
    feedbacks: Iterable[InterviewFeedback],
    dimension_evaluations: Mapping[str, ScoreEvaluation],
    evidence_refs: Iterable[ReportEvidenceRefV2],
    role_relevance_by_dimension: Mapping[str, str] | None = None,
) -> list[ReportObservationV2]:
    """Aggregate bounded cross-question signals without copying candidate facts."""

    feedbacks = list(feedbacks)
    evidence_refs = list(evidence_refs)
    evidence_ids = {item.evidence_ref_id for item in evidence_refs}
    role_relevance = {
        **DEFAULT_ROLE_RELEVANCE,
        **dict(role_relevance_by_dimension or {}),
    }
    candidates: dict[tuple[str, str, str], dict[str, object]] = {}

    for feedback in feedbacks:
        answer_ref = f"candidate:{feedback.question_id}:answer"
        if answer_ref not in evidence_ids or feedback.answer_state != "answered":
            answer_ref = ""
        knowledge_refs = sorted(
            {
                f"reference:{reference.chunk_id}"
                for reference in feedback.references
                if f"reference:{reference.chunk_id}" in evidence_ids
            }
        )
        for raw in feedback.dimension_evidence:
            if not isinstance(raw, dict):
                continue
            dimension = str(raw.get("dimension") or "")
            if dimension not in REPORT_DIMENSIONS:
                continue
            evaluation = feedback.dimension_evaluations.get(dimension)
            if evaluation is not None and evaluation.status != "evaluated":
                continue
            signals = {
                str(signal)
                for signal in raw.get("quality_signals", [])
                if str(signal) in SIGNAL_TOPICS
            }
            if answer_ref and any(str(value).strip() for value in raw.get("observed", [])):
                for signal in sorted(signals):
                    _add_candidate(
                        candidates,
                        observation_type="strength",
                        dimension=dimension,
                        topic=SIGNAL_TOPICS[signal],
                        severity="low",
                        question_id=feedback.question_id,
                        answer_ref=answer_ref,
                        knowledge_refs=knowledge_refs,
                    )
            if answer_ref:
                for missing in raw.get("missing", []):
                    text = str(missing).strip()
                    if not text:
                        continue
                    observation_type = "risk" if _is_severe_risk(text) else "gap"
                    topic = (
                        _topic_from_text(text) or "risk_identification"
                        if observation_type == "risk"
                        else _normalized_topic(text, dimension=dimension)
                    )
                    _add_candidate(
                        candidates,
                        observation_type=observation_type,
                        dimension=dimension,
                        topic=topic,
                        severity="high" if observation_type == "risk" else "medium",
                        question_id=feedback.question_id,
                        answer_ref=answer_ref,
                        knowledge_refs=knowledge_refs,
                    )

        if answer_ref:
            for highlight in feedback.highlights:
                topic = _topic_from_text(str(highlight))
                if topic:
                    dimension = _topic_dimension(topic, feedback)
                    if dimension is None:
                        continue
                    _add_candidate(
                        candidates,
                        observation_type="strength",
                        dimension=dimension,
                        topic=topic,
                        severity="low",
                        question_id=feedback.question_id,
                        answer_ref=answer_ref,
                        knowledge_refs=knowledge_refs,
                    )
            critique_topic = _topic_from_text(feedback.critique)
            if critique_topic:
                critique_type = (
                    "risk" if _is_severe_risk(feedback.critique) else "gap"
                )
                dimension = _topic_dimension(critique_topic, feedback)
                if dimension is None:
                    continue
                _add_candidate(
                    candidates,
                    observation_type=critique_type,
                    dimension=dimension,
                    topic=critique_topic,
                    severity="high" if critique_type == "risk" else "medium",
                    question_id=feedback.question_id,
                    answer_ref=answer_ref,
                    knowledge_refs=knowledge_refs,
                )

    for dimension in REPORT_DIMENSIONS:
        evaluation = dimension_evaluations.get(dimension)
        if evaluation is None or evaluation.status == "evaluated":
            continue
        question_refs = sorted(
            {
                feedback.question_id
                for feedback in feedbacks
                if dimension in feedback.dimension_evaluations
                and feedback.dimension_evaluations[dimension].status != "evaluated"
            }
        )
        if not question_refs:
            continue
        key = ("limitation", dimension, f"coverage_{dimension}")
        candidates[key] = {
            "question_refs": set(question_refs),
            "answer_evidence_refs": set(),
            "knowledge_refs": set(),
            "severity": "medium" if evaluation.status == "insufficient_evidence" else "low",
        }

    observations = [
        _finalize_observation(
            key,
            item,
            role_relevance=str(role_relevance.get(key[1], "medium")),
        )
        for key, item in candidates.items()
    ]
    return sorted(
        observations,
        key=lambda item: (
            -SEVERITY_ORDER[item.severity],
            TYPE_ORDER[item.type],
            item.dimension,
            item.normalized_topic,
            item.observation_id,
        ),
    )


def _add_candidate(
    candidates: dict[tuple[str, str, str], dict[str, object]],
    *,
    observation_type: str,
    dimension: str,
    topic: str,
    severity: str,
    question_id: str,
    answer_ref: str,
    knowledge_refs: Iterable[str],
) -> None:
    if not answer_ref:
        return
    key = (observation_type, dimension, topic)
    item = candidates.setdefault(
        key,
        {
            "question_refs": set(),
            "answer_evidence_refs": set(),
            "knowledge_refs": set(),
            "severity": severity,
        },
    )
    item["question_refs"].add(question_id)  # type: ignore[union-attr]
    item["answer_evidence_refs"].add(answer_ref)  # type: ignore[union-attr]
    item["knowledge_refs"].update(knowledge_refs)  # type: ignore[union-attr]
    if SEVERITY_ORDER[severity] > SEVERITY_ORDER[str(item["severity"])]:
        item["severity"] = severity


def _finalize_observation(
    key: tuple[str, str, str],
    item: Mapping[str, object],
    *,
    role_relevance: str,
) -> ReportObservationV2:
    observation_type, dimension, topic = key
    question_refs = sorted(item["question_refs"])
    answer_refs = sorted(item["answer_evidence_refs"])
    knowledge_refs = sorted(item["knowledge_refs"])
    frequency = max(1, len(question_refs))
    severity = str(item["severity"])
    if frequency >= 2 and severity == "low":
        severity = "medium"
    evidence_count = len(answer_refs) + len(knowledge_refs)
    evidence_strength = (
        "high" if evidence_count >= 3 else "medium" if evidence_count >= 2 else "low"
    )
    confidence_band = evidence_strength
    identity = "|".join(key)
    observation_id = "obs-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
    return ReportObservationV2(
        observation_id=observation_id,
        type=observation_type,
        dimension=dimension,
        normalized_topic=topic,
        severity=severity,
        frequency=frequency,
        role_relevance=role_relevance,
        evidence_strength=evidence_strength,
        question_refs=question_refs,
        answer_evidence_refs=answer_refs,
        knowledge_refs=knowledge_refs,
        confidence_band=confidence_band,
    )


def _normalized_topic(text: str, *, dimension: str) -> str:
    code = text.partition(":")[0].strip().lower()
    return MISSING_CODE_TOPICS.get(code) or _topic_from_text(text) or f"{dimension}_completeness"


def _topic_from_text(text: str) -> str | None:
    normalized = re.sub(r"\s+", " ", str(text).strip().lower())
    for topic, terms in TOPIC_TERMS:
        if any(_contains_term(normalized, term) for term in terms):
            return topic
    return None


def _contains_term(normalized: str, term: str) -> bool:
    if any(ord(character) > 127 for character in term):
        return term in normalized
    if term in {"recover", "scalab"}:
        return term in normalized
    return re.search(
        rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])",
        normalized,
    ) is not None


def _is_severe_risk(text: str) -> bool:
    normalized = str(text).lower()
    return any(term in normalized for term in SEVERE_RISK_TERMS)


def _topic_dimension(
    topic: str, feedback: InterviewFeedback
) -> str | None:
    preferred = {
        "communication_clarity": "communication",
        "architecture_design": "architecture",
        "production_operations": "engineering",
        "recovery_strategy": "engineering",
        "measurable_outcomes": "engineering",
        "tradeoff_analysis": "depth",
        "risk_identification": "engineering",
        "technical_specificity": "depth",
    }.get(topic)
    if preferred:
        evaluation = feedback.dimension_evaluations.get(preferred)
        if evaluation is None or evaluation.status != "evaluated":
            return None
        return preferred
    for dimension in REPORT_DIMENSIONS:
        evaluation = feedback.dimension_evaluations.get(dimension)
        if evaluation is not None and evaluation.status == "evaluated":
            return dimension
    return None
