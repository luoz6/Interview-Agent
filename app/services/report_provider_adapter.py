from typing import Any

from pydantic import BaseModel, Field

from app.services.report_contract import CanonicalQuestionResult
from app.services.report_rule_score import (
    DimensionEvidence,
    apply_rule_quality_signals,
    score_question_from_evidence,
    score_question_without_evidence,
)


class ProviderQuestionResult(BaseModel):
    question_id: str
    question_text: str | None = None
    score: int | None = None
    dimension_scores: dict[str, int] | None = None
    dimension_evidence: list[DimensionEvidence] = Field(default_factory=list)
    rationale: str | None = None
    critique: str | None = None
    better_answer: str | None = None
    reference_chunk_ids: list[str] = Field(default_factory=list)
    references: list[str | dict[str, str]] = Field(default_factory=list)
    gaps: list[dict[str, Any]] = Field(default_factory=list)
    strengths: list[str] = Field(default_factory=list)
    weaknesses: list[str] = Field(default_factory=list)
    suggested_improvements: str | None = None
    highlights: list[str] = Field(default_factory=list)


class ProviderQuestionResultsEnvelope(BaseModel):
    session_id: str | None = None
    question_results: list[ProviderQuestionResult] = Field(default_factory=list)
    feedbacks: list[dict[str, Any]] = Field(default_factory=list)
    feedback_items: list[dict[str, Any]] = Field(default_factory=list)
    evaluation_results: list[dict[str, Any]] = Field(default_factory=list)
    references: list[str | dict[str, str]] = Field(default_factory=list)


class ProviderPayloadResult(BaseModel):
    question_results: list[CanonicalQuestionResult]
    reference_lookup: dict[str, dict[str, str]]
    provider_reference_ids: list[str]


def normalize_provider_payload(
    payload: dict[str, Any] | ProviderQuestionResultsEnvelope,
    evaluation_items: list[dict[str, Any]],
) -> ProviderPayloadResult:
    if isinstance(payload, ProviderQuestionResultsEnvelope):
        payload = payload.model_dump(exclude_none=True)

    provider_reference_ids = collect_provider_reference_ids(payload)
    reference_lookup = build_reference_lookup(
        payload,
        evaluation_items,
        provider_reference_ids,
    )
    raw_results = (
        payload.get("question_results")
        or payload.get("feedbacks")
        or payload.get("feedback_items")
        or payload.get("evaluation_results")
        or []
    )
    shared_highlights = (
        [
            str(value).strip()
            for value in payload.get("highlights", [])
            if str(value).strip()
        ]
        if len(raw_results) == 1 and isinstance(payload.get("highlights"), list)
        else []
    )
    question_results = [
        _normalize_question_result(
            item,
            evaluation_items,
            reference_lookup,
            default_highlights=shared_highlights,
        )
        for item in raw_results
        if isinstance(item, dict)
    ]
    return ProviderPayloadResult(
        question_results=question_results,
        reference_lookup=reference_lookup,
        provider_reference_ids=provider_reference_ids,
    )


def collect_provider_reference_ids(payload: dict[str, Any]) -> list[str]:
    reference_ids: list[str] = []
    for reference in payload.get("references", []):
        if isinstance(reference, str) and reference not in reference_ids:
            reference_ids.append(reference)
        elif isinstance(reference, dict):
            chunk_id = reference.get("chunk_id")
            if isinstance(chunk_id, str) and chunk_id not in reference_ids:
                reference_ids.append(chunk_id)
    return reference_ids


def build_reference_lookup(
    payload: dict[str, Any],
    evaluation_items: list[dict[str, Any]],
    provider_reference_ids: list[str],
) -> dict[str, dict[str, str]]:
    lookup: dict[str, dict[str, str]] = {}
    for reference in payload.get("references", []):
        if isinstance(reference, dict):
            normalized = _normalize_reference(reference)
            if normalized is not None:
                lookup[normalized["chunk_id"]] = normalized

    for item in evaluation_items:
        for key in ("scoring_references", "answer_references"):
            for reference in item.get(key, []):
                if not isinstance(reference, dict):
                    continue
                normalized = _normalize_reference(reference)
                if normalized is None:
                    continue
                if provider_reference_ids and normalized["chunk_id"] not in provider_reference_ids:
                    continue
                lookup[normalized["chunk_id"]] = normalized

    return lookup


def _normalize_question_result(
    item: dict[str, Any],
    evaluation_items: list[dict[str, Any]],
    reference_lookup: dict[str, dict[str, str]],
    *,
    default_highlights: list[str],
) -> CanonicalQuestionResult:
    evaluation_item = next(
        (
            candidate
            for candidate in evaluation_items
            if candidate.get("question_id") == item.get("question_id")
        ),
        {},
    )
    evidence_items = _normalize_dimension_evidence(item)
    evidence_items = apply_rule_quality_signals(evaluation_item, evidence_items)
    if evidence_items:
        scoring = score_question_from_evidence(evaluation_item, evidence_items)
        dimension_scores = scoring.dimension_scores
        score = scoring.score
        applicable_dimensions = list(scoring.applicable_dimensions)
    else:
        scoring = score_question_without_evidence(evaluation_item)
        dimension_scores = scoring.dimension_scores
        score = scoring.score
        applicable_dimensions = list(scoring.applicable_dimensions)
    reference_chunk_ids = _collect_reference_chunk_ids(
        item,
        evaluation_item,
        reference_lookup,
    )
    highlights = [
        str(value).strip()
        for value in item.get("highlights", [])
        if str(value).strip()
    ] or list(default_highlights)
    return CanonicalQuestionResult(
        question_id=item["question_id"],
        question_text=item.get("question_text")
        or evaluation_item.get("question_text")
        or item["question_id"],
        user_answer=_build_user_answer(evaluation_item),
        score=score,
        dimension_scores=dimension_scores,
        applicable_dimensions=applicable_dimensions,
        dimension_evidence=[evidence.model_dump() for evidence in evidence_items],
        rationale=item.get("rationale") or _build_rationale(item),
        critique=item.get("critique") or _build_critique(item),
        better_answer=item.get("better_answer")
        or item.get("suggested_improvements")
        or _build_better_answer(reference_chunk_ids, reference_lookup),
        reference_chunk_ids=reference_chunk_ids,
        highlights=highlights,
    )


def _normalize_dimension_evidence(item: dict[str, Any]) -> list[DimensionEvidence]:
    evidence_items = item.get("dimension_evidence") or []
    normalized: list[DimensionEvidence] = []
    for evidence in evidence_items:
        if isinstance(evidence, DimensionEvidence):
            normalized.append(evidence)
            continue
        if not isinstance(evidence, dict):
            continue
        normalized.append(DimensionEvidence.model_validate(evidence))
    return normalized


def _collect_reference_chunk_ids(
    item: dict[str, Any],
    evaluation_item: dict[str, Any],
    reference_lookup: dict[str, dict[str, str]],
) -> list[str]:
    chunk_ids: list[str] = []
    for reference in item.get("references", []):
        if isinstance(reference, str) and reference in reference_lookup and reference not in chunk_ids:
            chunk_ids.append(reference)
        elif isinstance(reference, dict):
            chunk_id = reference.get("chunk_id")
            if isinstance(chunk_id, str) and chunk_id in reference_lookup and chunk_id not in chunk_ids:
                chunk_ids.append(chunk_id)

    for chunk_id in item.get("reference_chunk_ids", []):
        if isinstance(chunk_id, str) and chunk_id in reference_lookup and chunk_id not in chunk_ids:
            chunk_ids.append(chunk_id)

    for gap in item.get("gaps", []):
        if isinstance(gap, dict):
            chunk_id = gap.get("reference_chunk_id")
            if isinstance(chunk_id, str) and chunk_id in reference_lookup and chunk_id not in chunk_ids:
                chunk_ids.append(chunk_id)

    if chunk_ids:
        return chunk_ids

    for key in ("scoring_references", "answer_references"):
        for reference in evaluation_item.get(key, []):
            if not isinstance(reference, dict):
                continue
            chunk_id = reference.get("chunk_id")
            if isinstance(chunk_id, str) and chunk_id in reference_lookup and chunk_id not in chunk_ids:
                chunk_ids.append(chunk_id)
    return chunk_ids


def _build_user_answer(evaluation_item: dict[str, Any]) -> str:
    if evaluation_item.get("answer_state") == "skipped":
        return "候选人跳过了这道题。"
    messages = evaluation_item.get("messages", [])
    answers = [
        str(message.get("content", "")).strip()
        for message in messages
        if message.get("role") == "candidate" and str(message.get("content", "")).strip()
    ]
    if answers:
        return " ".join(answers)
    return "候选人未作答这道题。"


def _build_rationale(item: dict[str, Any]) -> str:
    strengths = [
        str(value).strip()
        for value in item.get("strengths", [])
        if str(value).strip()
    ]
    weaknesses = [
        str(value).strip()
        for value in item.get("weaknesses", [])
        if str(value).strip()
    ]
    parts: list[str] = []
    if strengths:
        parts.append("优点：" + " ".join(strengths))
    if weaknesses:
        parts.append("不足：" + " ".join(weaknesses))
    return " ".join(parts) or "模型输出未提供评分依据。"


def _build_critique(item: dict[str, Any]) -> str:
    weaknesses = [
        str(value).strip()
        for value in item.get("weaknesses", [])
        if str(value).strip()
    ]
    if weaknesses:
        return weaknesses[0]
    critique = str(item.get("critique") or "").strip()
    if critique:
        return critique
    return "模型输出未提供明确问题点。"


def _build_better_answer(
    reference_chunk_ids: list[str],
    reference_lookup: dict[str, dict[str, str]],
) -> str:
    for chunk_id in reference_chunk_ids:
        reference = reference_lookup.get(chunk_id, {})
        if str(reference.get("source_type") or "").strip() != "answer":
            continue
        excerpt = str(reference.get("excerpt") or "").strip()
        if excerpt:
            return excerpt
    for chunk_id in reference_chunk_ids:
        excerpt = str(reference_lookup.get(chunk_id, {}).get("excerpt") or "").strip()
        if excerpt:
            return excerpt
    return "补充回退策略、一致性取舍和风险缓解细节。"


def _normalize_reference(reference: dict[str, Any]) -> dict[str, str] | None:
    chunk_id = str(reference.get("chunk_id") or "").strip()
    if not chunk_id:
        return None
    title = str(reference.get("title") or chunk_id).strip()
    source_type = str(reference.get("source_type") or "reference").strip()
    excerpt = str(
        reference.get("excerpt")
        or reference.get("content")
        or reference.get("missing")
        or title
    ).strip()
    return {
        "chunk_id": chunk_id,
        "title": title,
        "source_type": source_type,
        "excerpt": excerpt,
    }
