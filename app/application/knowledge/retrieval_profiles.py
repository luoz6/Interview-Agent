from app.application.knowledge.diagnostic_models import HybridFusionMode
from app.domain.knowledge.retrieval import (
    ResolvedRetrievalProfile,
    RetrievalIntent,
)


def compatibility_profile(*, minimum_score: float, evidence_limit: int) -> ResolvedRetrievalProfile:
    return ResolvedRetrievalProfile(
        profile_id="legacy-compatibility",
        profile_version="legacy-v1",
        semantic_candidate_limit=max(12, evidence_limit),
        fusion_candidate_limit=max(12, evidence_limit),
        rerank_candidate_limit=max(12, evidence_limit),
        evidence_limit=evidence_limit,
        minimum_score=minimum_score,
    )


def resolve_diagnostic_profile(
    runtime_profile: ResolvedRetrievalProfile,
    mode: HybridFusionMode,
) -> ResolvedRetrievalProfile:
    if mode is HybridFusionMode.FIXED_WEIGHTED_RRF:
        query_aware_fusion = False
    elif mode is HybridFusionMode.QUERY_AWARE_WEIGHTED_RRF:
        query_aware_fusion = True
    else:
        raise ValueError("unsupported hybrid fusion mode")
    return runtime_profile.model_copy(
        update={"query_aware_fusion": query_aware_fusion}
    )


PREP_PROFILE = ResolvedRetrievalProfile(
    profile_id="prep",
    profile_version="hybrid-v1",
    semantic_enabled=True,
    lexical_enabled=True,
    semantic_candidate_limit=20,
    lexical_candidate_limit=20,
    fusion_candidate_limit=15,
    rerank_candidate_limit=12,
    evidence_limit=8,
    minimum_score=0.45,
)

FOLLOWUP_PROFILE = PREP_PROFILE.model_copy(
    update={
        "profile_id": "followup",
        "semantic_candidate_limit": 12,
        "lexical_candidate_limit": 12,
        "fusion_candidate_limit": 8,
        "rerank_candidate_limit": 8,
        "evidence_limit": 4,
        "total_timeout_ms": 1500,
    }
)

QUESTION_REVIEW_PROFILE = PREP_PROFILE.model_copy(
    update={
        "profile_id": "question-review",
        "semantic_candidate_limit": 15,
        "lexical_candidate_limit": 15,
        "fusion_candidate_limit": 10,
        "rerank_candidate_limit": 8,
        "evidence_limit": 5,
    }
)

REPORT_REPAIR_PROFILE = QUESTION_REVIEW_PROFILE.model_copy(
    update={
        "profile_id": "report-repair",
        "semantic_candidate_limit": 8,
        "lexical_candidate_limit": 8,
        "fusion_candidate_limit": 6,
        "evidence_limit": 3,
    }
)


_PROFILE_BY_INTENT = {
    RetrievalIntent.PREP: PREP_PROFILE,
    RetrievalIntent.FOLLOWUP: FOLLOWUP_PROFILE,
    RetrievalIntent.QUESTION_REVIEW: QUESTION_REVIEW_PROFILE,
    RetrievalIntent.EVAL: QUESTION_REVIEW_PROFILE,
    RetrievalIntent.REPORT_REPAIR: REPORT_REPAIR_PROFILE,
}


def resolve_runtime_profile(
    intent: RetrievalIntent,
    settings,
    *,
    evidence_limit: int | None = None,
) -> ResolvedRetrievalProfile:
    base = _PROFILE_BY_INTENT.get(intent, PREP_PROFILE)
    configured = {
        RetrievalIntent.PREP: settings.profile_prep,
        RetrievalIntent.FOLLOWUP: settings.profile_followup,
        RetrievalIntent.QUESTION_REVIEW: settings.profile_question_review,
        RetrievalIntent.EVAL: settings.profile_question_review,
        RetrievalIntent.REPORT_REPAIR: settings.profile_report_repair,
    }.get(intent, settings.profile_prep)
    budget = {
        RetrievalIntent.PREP: settings.prep_budget,
        RetrievalIntent.FOLLOWUP: settings.followup_budget,
        RetrievalIntent.QUESTION_REVIEW: settings.question_review_budget,
        RetrievalIntent.EVAL: settings.question_review_budget,
        RetrievalIntent.REPORT_REPAIR: settings.report_repair_budget,
    }.get(intent, settings.prep_budget)
    profile_id, separator, version = configured.rpartition("@")
    if not separator or not profile_id.strip() or not version.strip():
        raise ValueError(f"invalid knowledge retrieval profile: {configured}")
    updates = {
        "profile_id": profile_id.strip(),
        "profile_version": version.strip(),
        "semantic_enabled": settings.semantic_enabled,
        "lexical_enabled": settings.lexical_enabled,
        "remote_reranker_enabled": settings.remote_reranker_enabled,
        "minimum_score": settings.minimum_score,
        "rrf_k": settings.rrf_k,
        "semantic_weight": settings.semantic_weight,
        "lexical_weight": settings.lexical_weight,
        "semantic_timeout_ms": budget.semantic_timeout_ms,
        "lexical_timeout_ms": budget.lexical_timeout_ms,
        "rerank_timeout_ms": budget.rerank_timeout_ms,
        "total_timeout_ms": budget.total_timeout_ms,
    }
    if evidence_limit is not None:
        updates["evidence_limit"] = max(1, int(evidence_limit))
    return ResolvedRetrievalProfile.model_validate(
        {**base.model_dump(), **updates}
    )
