from __future__ import annotations

from dataclasses import dataclass, field
from time import perf_counter
from typing import Literal
from uuid import uuid4

from app.ports.runtime import KnowledgeRepository
from app.services.knowledge_profile import CANONICAL_TAXONOMY
from app.services.knowledge_query import KnowledgeQuery
from app.services.prep import (
    InterviewPlan,
    KnowledgeBindingSnapshot,
    KnowledgeEvidenceRef,
    KnowledgeQuerySnapshot,
    PrepContext,
    PrepKnowledgeTopic,
    PrepQuestionHint,
    RoleProfile,
    deterministic_follow_up_hint,
)
from app.services.vector_store import KnowledgeChunk


RetrievalStatus = Literal["completed", "empty", "degraded"]


@dataclass
class QueryRetrieval:
    query: KnowledgeQuery
    chunks: list[KnowledgeChunk] = field(default_factory=list)
    status: RetrievalStatus = "empty"
    degraded_reason: str | None = None
    latency_ms: float = 0.0


@dataclass
class GroundedCandidate:
    chunk: KnowledgeChunk
    topic_ids: list[str] = field(default_factory=list)
    canonical_tags: list[str] = field(default_factory=list)


@dataclass
class GroundingResult:
    retrievals: list[QueryRetrieval]
    candidates: list[GroundedCandidate]
    status: RetrievalStatus
    degraded_reason: str | None
    corpus_manifest_sha256: str


def retrieve_grounding(
    queries: list[KnowledgeQuery],
    repository: KnowledgeRepository,
) -> GroundingResult:
    retrievals: list[QueryRetrieval] = []
    candidate_lookup: dict[str, GroundedCandidate] = {}
    corpus_manifest_sha256 = ""
    overall_degraded_reason: str | None = None

    for query in queries:
        started_at = perf_counter()
        try:
            raw_chunks = repository.search(
                query.query_text,
                job_tags=query.filters.get("tags", []),
                source_types=query.source_types,
                limit=query.top_k,
            )
        except Exception:
            retrievals.append(
                QueryRetrieval(
                    query=query,
                    status="degraded",
                    degraded_reason="knowledge_unavailable",
                    latency_ms=round((perf_counter() - started_at) * 1000, 3),
                )
            )
            overall_degraded_reason = overall_degraded_reason or "knowledge_unavailable"
            continue

        trusted: list[KnowledgeChunk] = []
        query_degraded_reason: str | None = None
        for raw_chunk in raw_chunks:
            try:
                chunk = (
                    raw_chunk
                    if isinstance(raw_chunk, KnowledgeChunk)
                    else KnowledgeChunk.model_validate(raw_chunk)
                )
            except Exception:
                query_degraded_reason = "invalid_knowledge_metadata"
                continue
            content_hash = chunk.metadata.get("content_sha256")
            manifest_hash = chunk.metadata.get("corpus_manifest_sha256")
            if not isinstance(content_hash, str) or not content_hash:
                query_degraded_reason = "invalid_knowledge_metadata"
                continue
            if not isinstance(manifest_hash, str) or not manifest_hash:
                query_degraded_reason = "invalid_knowledge_metadata"
                continue
            if corpus_manifest_sha256 and manifest_hash != corpus_manifest_sha256:
                query_degraded_reason = "corpus_manifest_mismatch"
                continue
            corpus_manifest_sha256 = manifest_hash
            trusted.append(chunk)
            candidate = candidate_lookup.setdefault(
                chunk.chunk_id,
                GroundedCandidate(chunk=chunk),
            )
            if query.topic_id not in candidate.topic_ids:
                candidate.topic_ids.append(query.topic_id)
            if query.canonical_tag not in candidate.canonical_tags:
                candidate.canonical_tags.append(query.canonical_tag)

        if query_degraded_reason:
            status: RetrievalStatus = "degraded"
            overall_degraded_reason = overall_degraded_reason or query_degraded_reason
        else:
            status = "completed" if trusted else "empty"
        retrievals.append(
            QueryRetrieval(
                query=query,
                chunks=trusted,
                status=status,
                degraded_reason=query_degraded_reason,
                latency_ms=round((perf_counter() - started_at) * 1000, 3),
            )
        )

    if overall_degraded_reason:
        overall_status: RetrievalStatus = "degraded"
    elif candidate_lookup:
        overall_status = "completed"
    else:
        overall_status = "empty"
    candidates = sorted(
        candidate_lookup.values(),
        key=lambda item: (-float(item.chunk.score or 0.0), item.chunk.chunk_id),
    )
    return GroundingResult(
        retrievals=retrievals,
        candidates=candidates,
        status=overall_status,
        degraded_reason=overall_degraded_reason,
        corpus_manifest_sha256=corpus_manifest_sha256,
    )


def provider_knowledge_context(result: GroundingResult) -> list[dict]:
    return [
        {
            "evidence_id": candidate.chunk.chunk_id,
            "title": candidate.chunk.title,
            "domain": candidate.chunk.domain,
            "source_type": candidate.chunk.source_type,
            "candidate_summary": _candidate_summary(candidate.chunk),
            "topic_ids": list(candidate.topic_ids),
        }
        for candidate in result.candidates
    ]


def attach_grounded_prep_context(
    plan: InterviewPlan,
    *,
    role_profile: RoleProfile,
    result: GroundingResult,
    prep_run_id: str | None = None,
) -> InterviewPlan:
    evidence_refs = [
        KnowledgeEvidenceRef(
            evidence_id=candidate.chunk.chunk_id,
            title=candidate.chunk.title,
            domain=candidate.chunk.domain,
            source_type=candidate.chunk.source_type,
            score=candidate.chunk.score,
            content_sha256=str(candidate.chunk.metadata["content_sha256"]),
            corpus_manifest_sha256=str(
                candidate.chunk.metadata["corpus_manifest_sha256"]
            ),
            candidate_summary=_candidate_summary(candidate.chunk),
        )
        for candidate in result.candidates
    ]
    topics = [_build_topic(retrieval) for retrieval in result.retrievals]
    question_hints = [
        _build_question_hint(question, result.candidates, role_profile)
        for question in plan.questions
    ]
    snapshot = KnowledgeBindingSnapshot(
        prep_run_id=prep_run_id or f"prep-{uuid4().hex}",
        corpus_manifest_sha256=result.corpus_manifest_sha256,
        queries=[_query_snapshot(retrieval) for retrieval in result.retrievals],
        status=result.status,
        degraded_reason=result.degraded_reason,
    )
    context = PrepContext(
        schema_version="v2",
        summary=_context_summary(result, len(question_hints)),
        knowledge_status=result.status,
        topics=topics,
        question_hints=question_hints,
        role_profile=role_profile,
        evidence_refs=evidence_refs,
        binding_snapshot=snapshot,
    )
    return plan.model_copy(update={"prep_context": context})


def degraded_grounding(queries: list[KnowledgeQuery], reason: str) -> GroundingResult:
    return GroundingResult(
        retrievals=[
            QueryRetrieval(query=query, status="degraded", degraded_reason=reason)
            for query in queries
        ],
        candidates=[],
        status="degraded",
        degraded_reason=reason,
        corpus_manifest_sha256="",
    )


def _build_topic(retrieval: QueryRetrieval) -> PrepKnowledgeTopic:
    tag = retrieval.query.canonical_tag
    label = CANONICAL_TAXONOMY.get(tag, {}).get("label", tag)
    evidence_ids = [chunk.chunk_id for chunk in retrieval.chunks]
    if evidence_ids:
        summary = f"Retrieved {len(evidence_ids)} trusted evidence items for {label}."
        source = "retrieval"
    else:
        summary = f"No trusted knowledge evidence was available for {label}."
        source = "keyword_fallback"
    return PrepKnowledgeTopic(
        id=retrieval.query.topic_id,
        label=label,
        source=source,
        evidence=summary,
        tags=[tag],
        evidence_ids=evidence_ids,
        candidate_summary=summary,
    )


def _build_question_hint(
    question,
    candidates: list[GroundedCandidate],
    role_profile: RoleProfile,
) -> PrepQuestionHint:
    selected = _select_candidates(question, candidates)
    topic_ids = _dedupe(
        topic_id for candidate in selected for topic_id in candidate.topic_ids
    )
    tags = _dedupe(
        tag for candidate in selected for tag in candidate.canonical_tags
    )
    if not tags:
        tags = _matching_profile_tags(question, role_profile)
    return PrepQuestionHint(
        question_id=question.id,
        topic_ids=topic_ids,
        follow_up_hints=[deterministic_follow_up_hint(tag) for tag in tags],
        evidence_titles=[candidate.chunk.title for candidate in selected],
        evidence_ids=[candidate.chunk.chunk_id for candidate in selected],
    )


def _select_candidates(question, candidates: list[GroundedCandidate]) -> list[GroundedCandidate]:
    if not candidates:
        return []
    text = f"{question.prompt} {question.focus}".lower().replace("-", " ")
    scored: list[tuple[int, GroundedCandidate]] = []
    for candidate in candidates:
        relevance = 0
        for tag in candidate.canonical_tags:
            if tag.replace("-", " ") in text:
                relevance += 100
        domain = candidate.chunk.domain.lower().replace("-", " ")
        if domain and domain in text:
            relevance += 40
        title_terms = {
            term
            for term in candidate.chunk.title.lower().replace("-", " ").split()
            if len(term) >= 4
        }
        relevance += 5 * sum(1 for term in title_terms if term in text)
        scored.append((relevance, candidate))
    relevant = [item for item in scored if item[0] > 0]
    if not relevant:
        return candidates[:1]
    relevant.sort(
        key=lambda item: (
            -item[0],
            -float(item[1].chunk.score or 0.0),
            item[1].chunk.chunk_id,
        )
    )
    return [candidate for _, candidate in relevant[:3]]


def _matching_profile_tags(question, role_profile: RoleProfile) -> list[str]:
    text = f"{question.prompt} {question.focus}".lower().replace("-", " ")
    matched = [
        tag
        for tag in role_profile.canonical_tags
        if tag.replace("-", " ") in text
    ]
    return matched[:3] or role_profile.canonical_tags[:1]


def _query_snapshot(retrieval: QueryRetrieval) -> KnowledgeQuerySnapshot:
    return KnowledgeQuerySnapshot(
        query_id=retrieval.query.query_id,
        topic_id=retrieval.query.topic_id,
        filters={
            "tags": retrieval.query.filters.get("tags", []),
            "source_types": retrieval.query.source_types,
        },
        top_k=retrieval.query.top_k,
        hit_ids=[chunk.chunk_id for chunk in retrieval.chunks],
        hit_content_sha256={
            chunk.chunk_id: str(chunk.metadata["content_sha256"])
            for chunk in retrieval.chunks
        },
        status=retrieval.status,
        degraded_reason=retrieval.degraded_reason,
    )


def _candidate_summary(chunk: KnowledgeChunk) -> str:
    content_kind = str(chunk.metadata.get("content_kind") or "knowledge")
    return (
        f"{chunk.title} provides {content_kind.replace('_', ' ')} evidence "
        f"for {chunk.domain} interview checks."
    )


def _context_summary(result: GroundingResult, question_count: int) -> str:
    if result.status == "completed":
        return (
            f"Knowledge Agent 预热了 {len(result.candidates)} 条可信知识证据，"
            f"并为 {question_count} 道题绑定了提问依据。"
        )
    if result.status == "degraded":
        return "知识检索已降级，Provider 生成的面试计划仍可使用。"
    return "知识检索未返回可信证据，本次计划未创建知识引用。"


def _dedupe(values) -> list[str]:
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result
