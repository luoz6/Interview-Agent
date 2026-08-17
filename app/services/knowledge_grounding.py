from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
import json
from time import perf_counter
from typing import Literal
from uuid import uuid4

from app.domain.knowledge.evidence import (
    BaseEvidenceBundle,
    EvidenceRef,
    QuestionEvidenceBinding,
    QuestionSourceScopeBinding,
)
from app.domain.knowledge.evidence_gate import RetrievalEvidenceGate
from app.ports.runtime import KnowledgeRepository
from app.services.knowledge_profile import CANONICAL_TAXONOMY
from app.domain.knowledge.models import KnowledgeChunk, KnowledgeQuery
from app.domain.knowledge.retrieval import RetrievalAvailability, RetrievalIntent
from app.domain.knowledge.source_scope import KnowledgeSourceScope
from app.domain.knowledge.user_material_lineage import (
    SHA256_PATTERN,
    declares_user_material,
    has_valid_user_material_lineage,
)
from app.domain.knowledge.engine import RuntimeEngineExecution
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


RetrievalStatus = Literal["completed", "empty", "degraded"]
CONTENT_KIND_LABELS = {
    "benchmark": "评估基准",
    "engineering_practice": "工程实践",
    "failure_mode": "故障模式",
    "hard_negative": "边界辨析",
    "mechanism": "机制",
    "knowledge": "知识",
}


@dataclass
class QueryRetrieval:
    query: KnowledgeQuery
    chunks: list[KnowledgeChunk] = field(default_factory=list)
    status: RetrievalStatus = "empty"
    degraded_reason: str | None = None
    latency_ms: float = 0.0
    retrieval_request_id: str | None = None
    retrieval_engine_version: str | None = None
    profile_version: str | None = None
    resolved_profile_snapshot: dict = field(default_factory=dict)
    component_versions: dict[str, str] = field(default_factory=dict)
    engine_execution: RuntimeEngineExecution | None = None


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
    knowledge_engine_execution: RuntimeEngineExecution | None = None


def retrieve_grounding(
    queries: list[KnowledgeQuery],
    repository: KnowledgeRepository,
    *,
    prep_run_id: str | None = None,
    expected_manifest_sha256: str | None = None,
    source_scope: KnowledgeSourceScope | None = None,
) -> GroundingResult:
    retrievals: list[QueryRetrieval] = []
    candidate_lookup: dict[str, GroundedCandidate] = {}
    corpus_manifest_sha256 = expected_manifest_sha256 or ""
    overall_degraded_reason: str | None = None
    execution: RuntimeEngineExecution | None = None

    for query in queries:
        started_at = perf_counter()
        runtime_degraded_reason: str | None = None
        try:
            if prep_run_id and callable(getattr(repository, "search_runtime", None)):
                runtime_kwargs = {
                    "intent": RetrievalIntent.PREP,
                    "job_tags": query.filters.get("tags", []),
                    "source_types": query.source_types,
                    "limit": query.top_k,
                    "prep_run_id": prep_run_id,
                }
                if source_scope is not None:
                    runtime_kwargs["source_scope"] = source_scope
                outcome = repository.search_runtime(
                    query.query_text,
                    **runtime_kwargs,
                )
                execution = outcome.execution
                raw_chunks = outcome.result.selected_evidence
                runtime_result = outcome.result
                fallback_reason = outcome.execution.fallback_reason
                runtime_reasons = [
                    *outcome.result.degraded_reasons,
                    *([fallback_reason.value] if fallback_reason is not None else []),
                ]
                if fallback_reason is not None:
                    runtime_degraded_reason = fallback_reason.value
                elif outcome.result.availability == RetrievalAvailability.DEGRADED:
                    runtime_degraded_reason = next(
                        iter(runtime_reasons), "knowledge_degraded"
                    )
                if runtime_degraded_reason:
                    overall_degraded_reason = (
                        overall_degraded_reason
                        or runtime_degraded_reason
                    )
            elif source_scope is None:
                raw_chunks = repository.search(
                    query.query_text,
                    job_tags=query.filters.get("tags", []),
                    source_types=query.source_types,
                    limit=query.top_k,
                )
                runtime_result = None
            else:
                raise RuntimeError("source-aware retrieval is unavailable")
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
        query_degraded_reason: str | None = runtime_degraded_reason
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
            if declares_user_material(chunk):
                if (
                    not has_valid_user_material_lineage(chunk)
                    or manifest_hash
                ):
                    query_degraded_reason = "invalid_knowledge_metadata"
                    continue
                trusted.append(chunk)
                candidate = candidate_lookup.setdefault(
                    chunk.chunk_id,
                    GroundedCandidate(chunk=chunk),
                )
                if query.topic_id not in candidate.topic_ids:
                    candidate.topic_ids.append(query.topic_id)
                if query.canonical_tag not in candidate.canonical_tags:
                    candidate.canonical_tags.append(query.canonical_tag)
                continue
            if not isinstance(content_hash, str) or not SHA256_PATTERN.fullmatch(
                content_hash
            ):
                query_degraded_reason = "invalid_knowledge_metadata"
                continue
            if not isinstance(manifest_hash, str) or not SHA256_PATTERN.fullmatch(
                manifest_hash
            ):
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
                retrieval_request_id=(
                    runtime_result.request_id if runtime_result is not None else None
                ),
                retrieval_engine_version=(
                    runtime_result.retrieval_engine_version
                    if runtime_result is not None
                    else "legacy-compatibility-v1"
                ),
                profile_version=(
                    runtime_result.profile_version
                    if runtime_result is not None
                    else "legacy-compatibility-v1"
                ),
                resolved_profile_snapshot=(
                    runtime_result.trace.resolved_profile.model_dump(mode="json")
                    if runtime_result is not None
                    and runtime_result.trace.resolved_profile is not None
                    else {}
                ),
                component_versions=(
                    runtime_result.trace.component_versions.model_dump(mode="json")
                    if runtime_result is not None
                    and runtime_result.trace.component_versions is not None
                    else {}
                ),
                engine_execution=(execution if runtime_result is not None else None),
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
        knowledge_engine_execution=execution,
    )


def supplement_question_grounding(
    plan: InterviewPlan,
    *,
    role_profile: RoleProfile,
    result: GroundingResult,
    repository: KnowledgeRepository,
    prep_run_id: str | None = None,
    source_scope: KnowledgeSourceScope | None = None,
) -> GroundingResult:
    """Retrieve only for questions that the role-level candidate pool cannot bind."""

    missing_questions = [
        question
        for question in plan.questions
        if not _select_candidates(question, result.candidates)
    ]
    if not missing_questions:
        return result
    supplemental_queries = [
        _question_specific_query(question, role_profile)
        for question in missing_questions
    ]
    supplemental = retrieve_grounding(
        supplemental_queries,
        repository,
        prep_run_id=prep_run_id,
        expected_manifest_sha256=result.corpus_manifest_sha256 or None,
        source_scope=source_scope,
    )
    return _merge_grounding_results(result, supplemental)


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
    source_scope: KnowledgeSourceScope | None = None,
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
                candidate.chunk.metadata.get("corpus_manifest_sha256") or ""
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
    resolved_prep_run_id = prep_run_id or f"prep-{uuid4().hex}"
    base_bundle = _base_evidence_bundle(result, resolved_prep_run_id)
    question_bindings = _question_evidence_bindings(
        question_hints,
        result.candidates,
        base_bundle,
        result.status,
        source_scope=source_scope,
    )
    snapshot = KnowledgeBindingSnapshot(
        prep_run_id=resolved_prep_run_id,
        corpus_manifest_sha256=result.corpus_manifest_sha256,
        queries=[_query_snapshot(retrieval) for retrieval in result.retrievals],
        status=result.status,
        degraded_reason=result.degraded_reason,
        knowledge_engine_execution=result.knowledge_engine_execution,
        base_evidence_bundle=base_bundle,
        question_evidence_bindings=question_bindings,
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
        summary = f"已为{label}找到 {len(evidence_ids)} 条可信知识证据。"
        source = "retrieval"
    else:
        summary = f"未找到可用于{label}的可信知识证据。"
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
        return []
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
        engine_execution=retrieval.engine_execution,
    )


def _question_specific_query(question, role_profile: RoleProfile) -> KnowledgeQuery:
    question_text = f"{question.prompt} {question.focus}".strip()
    normalized = question_text.casefold().replace("-", " ")
    matched_tags = [
        tag
        for tag in role_profile.canonical_tags
        if tag.replace("-", " ") in normalized
        or str(CANONICAL_TAXONOMY.get(tag, {}).get("label") or "").casefold()
        in normalized
    ]
    canonical_tag = matched_tags[0] if matched_tags else "general"
    identity = {
        "question_id": question.id,
        "query_sha256": sha256(question_text.encode("utf-8")).hexdigest(),
        "canonical_tag": canonical_tag,
    }
    return KnowledgeQuery(
        query_id=f"question-kq-{_stable_sha256(identity)[:16]}",
        topic_id=f"question-{question.id}",
        query_text=question_text[:4000],
        canonical_tag=canonical_tag,
        filters={"tags": matched_tags[:1]} if matched_tags else {},
        top_k=5,
    )


def _merge_grounding_results(
    role_result: GroundingResult,
    supplemental: GroundingResult,
) -> GroundingResult:
    candidate_lookup: dict[str, GroundedCandidate] = {}
    for source in (*role_result.candidates, *supplemental.candidates):
        candidate = candidate_lookup.setdefault(
            source.chunk.chunk_id,
            GroundedCandidate(chunk=source.chunk),
        )
        candidate.topic_ids = _dedupe((*candidate.topic_ids, *source.topic_ids))
        candidate.canonical_tags = _dedupe(
            (*candidate.canonical_tags, *source.canonical_tags)
        )
        if float(source.chunk.score or 0.0) > float(candidate.chunk.score or 0.0):
            candidate.chunk = source.chunk
    candidates = sorted(
        candidate_lookup.values(),
        key=lambda item: (-float(item.chunk.score or 0.0), item.chunk.chunk_id),
    )
    degraded_reason = role_result.degraded_reason or supplemental.degraded_reason
    if degraded_reason:
        status: RetrievalStatus = "degraded"
    elif candidates:
        status = "completed"
    else:
        status = "empty"
    return GroundingResult(
        retrievals=[*role_result.retrievals, *supplemental.retrievals],
        candidates=candidates,
        status=status,
        degraded_reason=degraded_reason,
        corpus_manifest_sha256=(
            role_result.corpus_manifest_sha256
            or supplemental.corpus_manifest_sha256
        ),
        knowledge_engine_execution=(
            supplemental.knowledge_engine_execution
            or role_result.knowledge_engine_execution
        ),
    )


def _base_evidence_bundle(
    result: GroundingResult,
    prep_run_id: str,
) -> BaseEvidenceBundle:
    query_facts = [
        {
            "query_id": retrieval.query.query_id,
            "topic_id": retrieval.query.topic_id,
            "query_sha256": sha256(
                retrieval.query.query_text.encode("utf-8")
            ).hexdigest(),
            "filter_keys": sorted(retrieval.query.filters),
            "source_types": sorted(retrieval.query.source_types),
            "top_k": retrieval.query.top_k,
            "retrieval_request_id": retrieval.retrieval_request_id,
            "retrieval_engine_version": retrieval.retrieval_engine_version,
            "profile_version": retrieval.profile_version,
            "component_versions": retrieval.component_versions,
        }
        for retrieval in result.retrievals
    ]
    query_sha256 = _stable_sha256(
        [item["query_sha256"] for item in query_facts]
    )
    request_ids = [
        retrieval.retrieval_request_id
        for retrieval in result.retrievals
        if retrieval.retrieval_request_id
    ]
    retrieval_request_id = (
        request_ids[0]
        if len(request_ids) == 1
        else f"prep-retrieval-set-{_stable_sha256(request_ids)[:24]}"
    )
    if not request_ids:
        retrieval_request_id = (
            f"legacy-prep-retrieval-set-"
            f"{_stable_sha256([prep_run_id, *[item['query_id'] for item in query_facts]])[:24]}"
        )
    engine_versions = {
        retrieval.retrieval_engine_version
        for retrieval in result.retrievals
        if retrieval.retrieval_engine_version
    }
    profile_versions = {
        retrieval.profile_version
        for retrieval in result.retrievals
        if retrieval.profile_version
    }
    profile_snapshots = [
        retrieval.resolved_profile_snapshot
        for retrieval in result.retrievals
        if retrieval.resolved_profile_snapshot
    ]
    component_versions = [
        retrieval.component_versions
        for retrieval in result.retrievals
        if retrieval.component_versions
    ]
    return BaseEvidenceBundle(
        retrieval_request_id=retrieval_request_id,
        prep_run_id=prep_run_id,
        query_sha256=query_sha256,
        structured_query_snapshot={"queries": query_facts},
        candidate_evidence_refs=tuple(
            EvidenceRef.from_chunk(candidate.chunk) for candidate in result.candidates
        ),
        retrieval_engine_version=(
            next(iter(engine_versions))
            if len(engine_versions) == 1
            else "mixed"
            if engine_versions
            else "unavailable"
        ),
        profile_version=(
            next(iter(profile_versions))
            if len(profile_versions) == 1
            else "mixed"
            if profile_versions
            else "unavailable"
        ),
        resolved_profile_snapshot=(
            profile_snapshots[0]
            if profile_snapshots
            and all(item == profile_snapshots[0] for item in profile_snapshots)
            else {}
        ),
        component_versions=(
            component_versions[0]
            if component_versions
            and all(item == component_versions[0] for item in component_versions)
            else {}
        ),
        corpus_manifest_sha256=result.corpus_manifest_sha256,
    )


def _question_evidence_bindings(
    hints: list[PrepQuestionHint],
    candidates: list[GroundedCandidate],
    bundle: BaseEvidenceBundle,
    status: RetrievalStatus,
    *,
    source_scope: KnowledgeSourceScope | None = None,
) -> list[QuestionEvidenceBinding]:
    chunk_lookup = {
        candidate.chunk.chunk_id: candidate.chunk for candidate in candidates
    }
    availability = {
        "completed": RetrievalAvailability.AVAILABLE,
        "empty": RetrievalAvailability.AVAILABLE,
        "degraded": RetrievalAvailability.DEGRADED,
    }[status]
    gate = RetrievalEvidenceGate()
    bindings: list[QuestionEvidenceBinding] = []
    bundle_ids = {ref.evidence_id for ref in bundle.candidate_evidence_refs}
    for hint in hints:
        if any(evidence_id not in bundle_ids for evidence_id in hint.evidence_ids):
            raise ValueError("question evidence binding references evidence outside bundle")
        selected = [chunk_lookup[evidence_id] for evidence_id in hint.evidence_ids]
        bindings.append(
            QuestionEvidenceBinding(
                bundle_id=bundle.bundle_id,
                question_id=hint.question_id,
                selected_evidence_ids=tuple(hint.evidence_ids),
                selection_version="question-evidence-selection-v1",
                source_scope_binding=_question_source_scope_binding(source_scope),
                decision=gate.decide_selection(availability, selected),
            )
        )
    return bindings


def _question_source_scope_binding(
    source_scope: KnowledgeSourceScope | None,
) -> QuestionSourceScopeBinding | None:
    if source_scope is None:
        return None
    if source_scope.usage != "question":
        raise ValueError("question binding requires a question source scope")
    has_system = source_scope.include_system_knowledge
    has_user = bool(source_scope.selected_documents)
    if has_system and has_user:
        scope_kind = "mixed"
    elif has_system:
        scope_kind = "system_only"
    elif has_user:
        scope_kind = "user_only"
    else:
        scope_kind = "explicit_empty"
    return QuestionSourceScopeBinding(
        scope_kind=scope_kind,
        source_scope_sha256=source_scope.source_scope_sha256,
    )


def _stable_sha256(value) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(payload.encode("utf-8")).hexdigest()


def _candidate_summary(chunk: KnowledgeChunk) -> str:
    content_kind = str(chunk.metadata.get("content_kind") or "knowledge")
    content_kind_label = CONTENT_KIND_LABELS.get(content_kind, "知识")
    domain_label = CANONICAL_TAXONOMY.get(chunk.domain, {}).get("label", chunk.domain)
    return f"{chunk.title}提供用于 {domain_label} 面试判断的{content_kind_label}证据。"


def _context_summary(result: GroundingResult, question_count: int) -> str:
    if result.status == "completed":
        return (
            f"知识智能体预热了 {len(result.candidates)} 条可信知识证据，"
            f"并为 {question_count} 道题绑定了提问依据。"
        )
    if result.status == "degraded":
        return "知识检索已降级，模型服务生成的面试计划仍可使用。"
    return "知识检索未返回可信证据，本次计划未创建知识引用。"


def _dedupe(values) -> list[str]:
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result
