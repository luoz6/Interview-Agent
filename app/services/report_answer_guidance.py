from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from typing import Iterable

from app.services.report import (
    InterviewFeedback,
    InterviewReport,
    ReportEvidenceRefV2,
    ReportMissingTechnicalPointV2,
    ReportObservationV2,
)


REPORT_ANSWER_GUIDANCE_VERSION = "report-answer-guidance-v1"
ANSWER_STRUCTURE_SUGGESTION = (
    "按背景、动作、取舍、验证四段组织回答；只复述原回答中已出现的经历事实。"
    "缺失内容保留为[真实背景]、[实际动作]、[实际取舍]、[实际指标值]，"
    "不要把参考资料写成自己的经历。"
)

TECHNICAL_POINT_TEXT = {
    "technical_foundation": "说明核心机制、触发条件和成立边界。",
    "structured_execution": "补齐动作顺序、依赖关系和验证步骤。",
    "tradeoff_analysis": "比较备选方案的收益、代价和适用条件。",
    "risk_identification": "说明失败模式、影响范围和缓解措施。",
    "recovery_strategy": "补充降级、回滚、恢复条件和恢复验证。",
    "measurable_outcomes": "补充基线、指标、观察窗口和验收条件；未知数字使用[实际指标值]。",
    "production_operations": "补充灰度、监控、告警和回滚闭环。",
    "technical_specificity": "补充关键接口、数据流、状态变化或实现细节。",
    "communication_clarity": "先给结论，再给不重复的依据和适用边界。",
    "architecture_design": "补充组件职责、关键数据流、容量假设和扩展边界。",
}

MEMORY_MARKERS = (
    "principal memory",
    "memory fact",
    "assistant memory",
    "长期记忆",
    "记忆事实",
)


@dataclass(frozen=True)
class AnswerGuidanceResult:
    feedbacks: list[InterviewFeedback]
    example_rewrite_published_count: int
    unsafe_rewrite_omitted_count: int


def build_structure_only_guidance() -> str:
    return _legacy_safe_guidance(points=[], example_rewrite=None)


def apply_report_answer_guidance(report: InterviewReport) -> InterviewReport:
    """Apply the same boundary policy to reports returned by injected/fake LLMs."""
    if report.report_schema_version != "report-schema-v2":
        return report
    result = apply_safe_answer_guidance(
        feedbacks=report.feedbacks,
        observations=report.technical_appendix.observations,
        evidence_refs=report.evidence_refs,
    )
    metadata = {
        **report.technical_appendix.metadata,
        "answer_guidance_version": REPORT_ANSWER_GUIDANCE_VERSION,
        "example_rewrite_published_count": (
            result.example_rewrite_published_count
        ),
        "unsafe_rewrite_omitted_count": result.unsafe_rewrite_omitted_count,
    }
    appendix = report.technical_appendix.model_copy(update={"metadata": metadata})
    return report.model_copy(
        update={"feedbacks": result.feedbacks, "technical_appendix": appendix}
    )


def apply_safe_answer_guidance(
    *,
    feedbacks: Iterable[InterviewFeedback],
    observations: Iterable[ReportObservationV2],
    evidence_refs: Iterable[ReportEvidenceRefV2],
) -> AnswerGuidanceResult:
    feedbacks = list(feedbacks)
    observations = list(observations)
    evidence_by_id = {item.evidence_ref_id: item for item in evidence_refs}
    evidence_ids = set(evidence_by_id)
    published = 0
    omitted = 0
    result: list[InterviewFeedback] = []

    for feedback in feedbacks:
        points = _technical_points(
            feedback.question_id,
            observations=observations,
            evidence_ids=evidence_ids,
        )
        example_rewrite, rewrite_refs = _supported_example_rewrite(
            proposed=feedback.better_answer,
            candidate_answer=feedback.user_answer,
            question_id=feedback.question_id,
            evidence_by_id=evidence_by_id,
        )
        if example_rewrite is not None:
            published += 1
        elif feedback.better_answer.strip():
            omitted += 1
        result.append(
            feedback.model_copy(
                update={
                    "answer_structure_suggestion": ANSWER_STRUCTURE_SUGGESTION,
                    "missing_technical_points": points,
                    "example_rewrite": example_rewrite,
                    "example_rewrite_evidence_refs": rewrite_refs,
                    "better_answer": _legacy_safe_guidance(
                        points=points,
                        example_rewrite=example_rewrite,
                    ),
                }
            )
        )

    return AnswerGuidanceResult(
        feedbacks=result,
        example_rewrite_published_count=published,
        unsafe_rewrite_omitted_count=omitted,
    )


def _technical_points(
    question_id: str,
    *,
    observations: list[ReportObservationV2],
    evidence_ids: set[str],
) -> list[ReportMissingTechnicalPointV2]:
    grouped: dict[str, list[ReportObservationV2]] = {}
    for observation in observations:
        if (
            observation.type not in {"gap", "risk"}
            or question_id not in observation.question_refs
            or not observation.answer_evidence_refs
        ):
            continue
        refs = {
            *observation.answer_evidence_refs,
            *observation.knowledge_refs,
        }
        if not refs.issubset(evidence_ids):
            continue
        grouped.setdefault(observation.normalized_topic, []).append(observation)

    points: list[ReportMissingTechnicalPointV2] = []
    for topic in sorted(grouped):
        items = sorted(grouped[topic], key=lambda item: item.observation_id)
        observation_refs = [item.observation_id for item in items]
        supporting_refs = sorted(
            {
                ref
                for item in items
                for ref in (
                    *item.answer_evidence_refs,
                    *item.knowledge_refs,
                )
            }
        )
        identity = f"{question_id}|{topic}"
        point_hash = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
        points.append(
            ReportMissingTechnicalPointV2(
                point_id=f"technical-point-{point_hash}",
                topic=topic,
                text=TECHNICAL_POINT_TEXT.get(
                    topic,
                    "补充该技术主题的机制、边界和验证方式。",
                ),
                observation_refs=observation_refs,
                evidence_refs=supporting_refs,
            )
        )
    return points


def _supported_example_rewrite(
    *,
    proposed: str,
    candidate_answer: str,
    question_id: str,
    evidence_by_id: dict[str, ReportEvidenceRefV2],
) -> tuple[str | None, list[str]]:
    proposed = proposed.strip()
    candidate_answer = candidate_answer.strip()
    candidate_ref = f"candidate:{question_id}:answer"
    candidate_evidence = evidence_by_id.get(candidate_ref)
    if (
        not proposed
        or not candidate_answer
        or candidate_evidence is None
        or candidate_evidence.namespace != "candidate"
        or candidate_evidence.question_id != question_id
        or _normalized_text(candidate_evidence.excerpt)
        != _normalized_text(candidate_answer)
        or _contains_memory_marker(proposed)
        or _normalized_text(proposed) != _normalized_text(candidate_answer)
    ):
        return None, []
    return proposed, [candidate_ref]


def _legacy_safe_guidance(
    *,
    points: list[ReportMissingTechnicalPointV2],
    example_rewrite: str | None,
) -> str:
    parts = [ANSWER_STRUCTURE_SUGGESTION]
    if points:
        parts.append(
            "需要补充的通用技术点："
            + " ".join(point.text for point in points)
        )
    if example_rewrite is not None:
        parts.append("仅基于原回答的可追溯示例：" + example_rewrite)
    else:
        parts.append("当前事实不足，本轮不生成自由经历改写。")
    return " ".join(parts)


def _contains_memory_marker(value: str) -> bool:
    normalized = value.casefold()
    return any(marker in normalized for marker in MEMORY_MARKERS)


def _normalized_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"\s+", " ", normalized).strip()
