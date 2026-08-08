from __future__ import annotations

import hashlib
import re
from typing import Any, Callable, Iterable, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.services.report import (
    ReportClaimV2,
    ReportCoverageV2,
    ReportEvidenceRefV2,
    ReportLimitationV2,
    ReportObservationV2,
)


REPORT_SUMMARY_PROMPT_VERSION = "report-cross-question-summary-v1"
REPORT_SUMMARY_PROMPT_TEMPLATE = """You summarize one interview round.
Use only the supplied structured observations, coverage, and evidence snippets.
Every conclusion, strength, and gap must cite observation_refs.
Do not invent candidate experience facts. Keep single-question signals scoped to that question.
Return structured JSON only."""
REPORT_SUMMARY_PROMPT_SHA256 = hashlib.sha256(
    REPORT_SUMMARY_PROMPT_TEMPLATE.encode("utf-8")
).hexdigest()


TOPIC_LABELS = {
    "technical_foundation": "技术基础",
    "structured_execution": "方案步骤与因果链",
    "tradeoff_analysis": "技术取舍",
    "risk_identification": "风险识别",
    "recovery_strategy": "故障恢复与兜底",
    "measurable_outcomes": "量化指标与验证",
    "production_operations": "生产运维与可观测性",
    "technical_specificity": "技术细节",
    "communication_clarity": "表达结构",
    "architecture_design": "架构设计",
    "breadth_completeness": "知识广度",
    "depth_completeness": "技术深度",
    "architecture_completeness": "架构能力",
    "engineering_completeness": "工程实践",
    "communication_completeness": "表达与沟通",
}
DIMENSION_LABELS = {
    "breadth": "知识广度",
    "depth": "技术深度",
    "architecture": "架构设计",
    "engineering": "工程实践",
    "communication": "表达与沟通",
}
SEVERITY_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}
RELEVANCE_ORDER = {"low": 0, "medium": 1, "high": 2}
EVIDENCE_ORDER = {"low": 0, "medium": 1, "high": 2}


class SummaryDraftClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=2000)
    observation_refs: list[str] = Field(min_length=1)


class SummaryDraftLimitation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=2000)
    observation_refs: list[str] = Field(min_length=1)


class SummaryDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conclusion: SummaryDraftClaim
    strengths: list[SummaryDraftClaim] = Field(default_factory=list, max_length=3)
    gaps: list[SummaryDraftClaim] = Field(default_factory=list, max_length=3)
    limitations: list[SummaryDraftLimitation] = Field(default_factory=list)


class ReportSummaryResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    summary: str = Field(min_length=1)
    summary_observations: list[ReportClaimV2]
    strengths: list[ReportClaimV2]
    limitations: list[ReportLimitationV2]
    generation_mode: Literal[
        "deterministic",
        "provider",
        "deterministic_fallback",
    ]
    degraded: bool
    reason_code: str
    prompt_version: str
    prompt_sha256: str
    provider_attempted: bool


def build_cross_question_summary(
    *,
    observations: Iterable[ReportObservationV2],
    coverage: ReportCoverageV2,
    evidence_refs: Iterable[ReportEvidenceRefV2],
    provider: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> ReportSummaryResult:
    observations = list(observations)
    evidence_refs = list(evidence_refs)
    _validate_observation_evidence_refs(observations, evidence_refs)
    if provider is not None:
        try:
            payload = build_summary_input(
                observations=observations,
                coverage=coverage,
                evidence_refs=evidence_refs,
            )
            draft = SummaryDraft.model_validate(provider(payload))
            return _summary_from_draft(
                draft,
                observations=observations,
                mode="provider",
            )
        except Exception:
            fallback = _deterministic_summary(
                observations=observations,
                coverage=coverage,
            )
            return fallback.model_copy(
                update={
                    "generation_mode": "deterministic_fallback",
                    "degraded": True,
                    "reason_code": "summary_generation_failed",
                    "provider_attempted": True,
                }
            )
    return _deterministic_summary(
        observations=observations,
        coverage=coverage,
    )


def build_summary_input(
    *,
    observations: Iterable[ReportObservationV2],
    coverage: ReportCoverageV2,
    evidence_refs: Iterable[ReportEvidenceRefV2],
) -> dict[str, Any]:
    observations = list(observations)
    referenced = {
        ref
        for observation in observations
        for ref in (
            *observation.answer_evidence_refs,
            *observation.knowledge_refs,
        )
    }
    snippets = [
        {
            "evidence_ref_id": item.evidence_ref_id,
            "namespace": item.namespace,
            "question_id": item.question_id,
            "excerpt": item.excerpt,
        }
        for item in sorted(evidence_refs, key=lambda value: value.evidence_ref_id)
        if item.evidence_ref_id in referenced
    ]
    return {
        "schema_version": "report-summary-input-v1",
        "prompt_version": REPORT_SUMMARY_PROMPT_VERSION,
        "prompt_sha256": REPORT_SUMMARY_PROMPT_SHA256,
        "coverage": coverage.model_dump(mode="json"),
        "observations": [
            item.model_dump(mode="json")
            for item in sorted(observations, key=lambda value: value.observation_id)
        ],
        "evidence_snippets": snippets,
    }


def _deterministic_summary(
    *,
    observations: list[ReportObservationV2],
    coverage: ReportCoverageV2,
) -> ReportSummaryResult:
    publishable = [
        item
        for item in observations
        if item.type in {"strength", "gap", "risk"}
        and item.answer_evidence_refs
    ]
    ranked = _deduplicate_observations(
        sorted(publishable, key=_observation_rank)
    )
    strengths_source = [item for item in ranked if item.type == "strength"]
    concern_source = [item for item in ranked if item.type in {"risk", "gap"}]
    top = concern_source[0] if concern_source else strengths_source[0] if strengths_source else None
    if top is not None:
        strengths_source = [
            item
            for item in strengths_source
            if item.observation_id != top.observation_id
        ]
    strengths_source = strengths_source[:3]
    summary_observations: list[ReportClaimV2] = []
    if top is not None:
        conclusion = _conclusion_claim(top)
        summary_observations.append(conclusion)
        for item in concern_source:
            if item.observation_id == top.observation_id:
                continue
            summary_observations.append(_observation_claim(item))
            if len(summary_observations) == 3:
                break
    strengths = [_observation_claim(item) for item in strengths_source]
    limitations = _limitations(observations, coverage=coverage)
    summary_parts = [item.text for item in summary_observations[:1]]
    if not summary_parts:
        summary_parts.append("本轮证据不足，未能形成可发布的跨题能力结论。")
    if len(summary_observations) > 1:
        summary_parts.append(summary_observations[1].text)
    if strengths:
        summary_parts.append(strengths[0].text)
    if limitations:
        summary_parts.append(limitations[0].text)
    return ReportSummaryResult(
        summary=" ".join(summary_parts),
        summary_observations=summary_observations,
        strengths=strengths,
        limitations=limitations,
        generation_mode="deterministic",
        degraded=False,
        reason_code="normal",
        prompt_version=REPORT_SUMMARY_PROMPT_VERSION,
        prompt_sha256=REPORT_SUMMARY_PROMPT_SHA256,
        provider_attempted=False,
    )


def _summary_from_draft(
    draft: SummaryDraft,
    *,
    observations: list[ReportObservationV2],
    mode: Literal["provider"],
) -> ReportSummaryResult:
    by_id = {item.observation_id: item for item in observations}
    conclusion = _resolve_claim(
        draft.conclusion,
        by_id=by_id,
        kind="conclusion",
        claim_id="summary-conclusion",
    )
    strengths = [
        _resolve_claim(
            item,
            by_id=by_id,
            kind="strength",
            claim_id=f"summary-strength-{index}",
            required_type="strength",
        )
        for index, item in enumerate(draft.strengths, 1)
    ]
    gaps = [
        _resolve_claim(
            item,
            by_id=by_id,
            kind="gap",
            claim_id=f"summary-gap-{index}",
            required_type=("gap", "risk"),
        )
        for index, item in enumerate(draft.gaps, 1)
    ]
    limitations = [
        _resolve_limitation(item, by_id=by_id, index=index)
        for index, item in enumerate(draft.limitations, 1)
    ]
    claims = [conclusion, *strengths, *gaps]
    _reject_duplicate_claims(claims)
    _reject_duplicate_topics(claims, by_id=by_id)
    return ReportSummaryResult(
        summary=" ".join(
            [conclusion.text]
            + ([gaps[0].text] if gaps else [])
            + ([strengths[0].text] if strengths else [])
            + ([limitations[0].text] if limitations else [])
        ),
        summary_observations=[conclusion, *gaps],
        strengths=strengths,
        limitations=limitations,
        generation_mode=mode,
        degraded=False,
        reason_code="normal",
        prompt_version=REPORT_SUMMARY_PROMPT_VERSION,
        prompt_sha256=REPORT_SUMMARY_PROMPT_SHA256,
        provider_attempted=True,
    )


def _resolve_claim(
    draft: SummaryDraftClaim,
    *,
    by_id: dict[str, ReportObservationV2],
    kind: str,
    claim_id: str,
    required_type: str | tuple[str, ...] | None = None,
) -> ReportClaimV2:
    resolved = _resolve_observations(draft.observation_refs, by_id=by_id)
    allowed = (
        {required_type}
        if isinstance(required_type, str)
        else set(required_type or ())
    )
    if allowed and any(item.type not in allowed for item in resolved):
        raise ValueError("summary claim references the wrong observation type")
    evidence_refs = _observation_evidence(resolved)
    if not evidence_refs:
        raise ValueError("summary claim has no answer evidence")
    return ReportClaimV2(
        claim_id=claim_id,
        kind=kind,
        text=draft.text.strip(),
        observation_refs=sorted({item.observation_id for item in resolved}),
        evidence_refs=evidence_refs,
    )


def _resolve_limitation(
    draft: SummaryDraftLimitation,
    *,
    by_id: dict[str, ReportObservationV2],
    index: int,
) -> ReportLimitationV2:
    resolved = _resolve_observations(
        draft.observation_refs,
        by_id=by_id,
    )
    if any(item.type != "limitation" for item in resolved):
        raise ValueError("summary limitation references a non-limitation")
    evidence_refs = _observation_evidence(resolved)
    return ReportLimitationV2(
        limitation_id=f"summary-limitation-{index}",
        text=draft.text.strip(),
        reason_code="insufficient_coverage",
        observation_refs=sorted({item.observation_id for item in resolved}),
        evidence_refs=evidence_refs,
    )


def _resolve_observations(
    refs: list[str],
    *,
    by_id: dict[str, ReportObservationV2],
) -> list[ReportObservationV2]:
    unique = sorted(set(refs))
    if not unique:
        raise ValueError("summary claim requires observation refs")
    try:
        return [by_id[item] for item in unique]
    except KeyError as exc:
        raise ValueError("summary contains an unknown observation ref") from exc


def _conclusion_claim(item: ReportObservationV2) -> ReportClaimV2:
    label = _topic_label(item.normalized_topic)
    if item.type == "risk":
        text = (
            f"本轮最需要优先核查的是{label}相关风险；结论仅限题目"
            f"{_questions(item)}中的已引用回答。"
        )
    elif item.type == "gap":
        text = (
            f"本轮最明确的改进信号是{label}不足；"
            + _scope_suffix(item)
        )
    else:
        text = (
            f"本轮最明确的优势信号是{label}；"
            + _scope_suffix(item)
        )
    return _claim(item, claim_id="summary-conclusion", kind="conclusion", text=text)


def _observation_claim(item: ReportObservationV2) -> ReportClaimV2:
    label = _topic_label(item.normalized_topic)
    if item.type == "strength":
        text = (
            f"在{item.frequency}道题中体现了{label}。"
            if item.frequency >= 2
            else f"在题目{_questions(item)}中体现了{label}；证据仅覆盖本题。"
        )
        kind = "strength"
    elif item.type == "risk":
        text = (
            f"在题目{_questions(item)}中出现{label}相关高风险信号；"
            "需要优先核查，且不扩展为未观察到的经历结论。"
        )
        kind = "risk"
    else:
        text = (
            f"在{item.frequency}道题中重复缺少{label}。"
            if item.frequency >= 2
            else f"在题目{_questions(item)}中缺少{label}；暂不扩展为整体能力判断。"
        )
        kind = "gap"
    return _claim(
        item,
        claim_id=f"summary-{kind}-{item.observation_id[4:]}",
        kind=kind,
        text=text,
    )


def _claim(
    item: ReportObservationV2,
    *,
    claim_id: str,
    kind: str,
    text: str,
) -> ReportClaimV2:
    return ReportClaimV2(
        claim_id=claim_id,
        kind=kind,
        text=text,
        observation_refs=[item.observation_id],
        evidence_refs=_observation_evidence([item]),
    )


def _limitations(
    observations: list[ReportObservationV2],
    *,
    coverage: ReportCoverageV2,
) -> list[ReportLimitationV2]:
    items = [item for item in observations if item.type == "limitation"]
    limitations = [
        ReportLimitationV2(
            limitation_id=f"summary-limitation-{item.observation_id[4:]}",
            text=f"本轮未充分考察{DIMENSION_LABELS[item.dimension]}。",
            reason_code=f"coverage_{item.dimension}",
            observation_refs=[item.observation_id],
            evidence_refs=_observation_evidence([item]),
        )
        for item in items
    ]
    if not limitations and coverage.status != "complete":
        limitations.append(
            ReportLimitationV2(
                limitation_id="summary-limitation-coverage",
                text="本轮覆盖不足，未充分考察所有能力维度。",
                reason_code="insufficient_coverage",
            )
        )
    return limitations


def _observation_evidence(
    observations: Iterable[ReportObservationV2],
) -> list[str]:
    return sorted(
        {
            ref
            for item in observations
            for ref in (*item.answer_evidence_refs, *item.knowledge_refs)
        }
    )


def _observation_rank(item: ReportObservationV2) -> tuple:
    return (
        0 if item.type == "risk" else 1 if item.type == "gap" else 2,
        -SEVERITY_ORDER[item.severity],
        -item.frequency,
        -RELEVANCE_ORDER[item.role_relevance],
        -EVIDENCE_ORDER[item.evidence_strength],
        item.dimension,
        item.normalized_topic,
        item.observation_id,
    )


def _deduplicate_observations(
    observations: Iterable[ReportObservationV2],
) -> list[ReportObservationV2]:
    result: list[ReportObservationV2] = []
    seen: set[tuple[str, str]] = set()
    for item in observations:
        key = (item.dimension, item.normalized_topic)
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _validate_observation_evidence_refs(
    observations: Iterable[ReportObservationV2],
    evidence_refs: Iterable[ReportEvidenceRefV2],
) -> None:
    available = {item.evidence_ref_id for item in evidence_refs}
    referenced = {
        ref
        for observation in observations
        for ref in (
            *observation.answer_evidence_refs,
            *observation.knowledge_refs,
        )
    }
    if not referenced.issubset(available):
        raise ValueError("summary observation contains an unpublished evidence ref")


def _topic_label(topic: str) -> str:
    return TOPIC_LABELS.get(topic, "该技术主题")


def _questions(item: ReportObservationV2) -> str:
    return "、".join(item.question_refs)


def _scope_suffix(item: ReportObservationV2) -> str:
    if item.frequency >= 2:
        return f"该信号覆盖{item.frequency}道题。"
    return f"该信号仅来自题目{_questions(item)}，不扩展为整体能力判断。"


def _reject_duplicate_claims(claims: list[ReportClaimV2]) -> None:
    normalized = [
        re.sub(r"\s+", "", item.text).lower()
        for item in claims
    ]
    if len(normalized) != len(set(normalized)):
        raise ValueError("summary contains duplicate claims")
    observation_sets = [set(item.observation_refs) for item in claims]
    for index, current in enumerate(observation_sets):
        if any(current & previous for previous in observation_sets[:index]):
            raise ValueError("summary repeats an observation across claims")


def _reject_duplicate_topics(
    claims: list[ReportClaimV2],
    *,
    by_id: dict[str, ReportObservationV2],
) -> None:
    topic_sets = [
        {
            (by_id[ref].dimension, by_id[ref].normalized_topic)
            for ref in claim.observation_refs
        }
        for claim in claims
    ]
    for index, current in enumerate(topic_sets):
        if any(current & previous for previous in topic_sets[:index]):
            raise ValueError("summary repeats a normalized topic across claims")
