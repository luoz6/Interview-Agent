from pydantic import ValidationError
import pytest

from app.domain.knowledge.evidence import (
    EvaluationConfidence,
    EvidenceAvailability,
    EvidenceSufficiency,
)
from app.domain.knowledge.evidence_gate import (
    build_evidence_sufficiency_signals,
    EvaluationSupportGate,
    RetrievalEvidenceGate,
)
from app.domain.knowledge.knowledge_unit import (
    EvaluationLevel,
    KnowledgeReviewStatus,
    KnowledgeUnit,
)
from app.domain.knowledge.models import KnowledgeChunk
from app.domain.knowledge.retrieval import (
    RetrievalAvailability,
    RetrievalCandidate,
    RetrievalIntent,
    RetrievalRequest,
    RetrievalResult,
    RetrievalRoutingHints,
    RetrievalTrace,
)


def _chunk(content="owner token atomic compare-and-delete", metadata=None):
    return KnowledgeChunk(
        chunk_id="redis-lock",
        title="Redis 分布式锁安全释放",
        content=content,
        source_type="theory",
        domain="redis",
        tags=["redis"],
        metadata=(
            {
                "content_sha256": "a" * 64,
                "corpus_manifest_sha256": "b" * 64,
            }
            if metadata is None
            else metadata
        ),
        score=0.9,
    )


def _result(availability=RetrievalAvailability.AVAILABLE, chunks=None):
    chunks = [] if chunks is None else chunks
    return RetrievalResult(
        request_id="request",
        availability=availability,
        selected_evidence=chunks,
        trace=RetrievalTrace(
            request_id="request",
            profile_id="review",
            profile_version="v1",
            latency_ms=1,
        ),
        retrieval_engine_version="hybrid-v2",
        profile_version="v1",
        latency_ms=1,
    )


def _candidate(
    *,
    chunk=None,
    semantic_score=0.8,
    lexical_score=None,
    semantic_rank=1,
    lexical_rank=None,
    channel_hits=("semantic",),
    matched_terms=(),
):
    return RetrievalCandidate(
        chunk=chunk or _chunk(),
        semantic_score=semantic_score,
        lexical_score=lexical_score,
        semantic_rank=semantic_rank,
        lexical_rank=lexical_rank,
        channel_hits=list(channel_hits),
        matched_terms=list(matched_terms),
    )


def _request(*, domain="redis", query="safe diagnostic query"):
    return RetrievalRequest(
        query_text=query,
        intent=RetrievalIntent.QUESTION_REVIEW,
        profile_id="review",
        routing_hints=RetrievalRoutingHints(domains=(domain,)),
    )


def _unit():
    return KnowledgeUnit(
        knowledge_unit_id="redis-distributed-lock",
        domain="redis",
        topic="distributed-lock",
        expected_signals=("owner token", "atomic compare-and-delete"),
        hard_negatives=("unconditional del",),
        expert_signals=("fencing token",),
        evaluation_levels=(
            EvaluationLevel(
                level="advanced",
                required_signals=(
                    "owner token",
                    "atomic compare-and-delete",
                    "fencing token",
                ),
            ),
        ),
        review_status=KnowledgeReviewStatus.REVIEWED,
    )


def test_knowledge_unit_rejects_unknown_rubric_signal():
    with pytest.raises(ValidationError, match="unknown signals"):
        KnowledgeUnit(
            knowledge_unit_id="redis-lock",
            domain="redis",
            topic="lock",
            expected_signals=("owner token",),
            evaluation_levels=(
                EvaluationLevel(level="advanced", required_signals=("unknown",)),
            ),
        )


def test_retrieval_gate_distinguishes_unavailable_empty_and_invalid_metadata():
    gate = RetrievalEvidenceGate()

    unavailable = gate.decide(_result(RetrievalAvailability.UNAVAILABLE))
    empty = gate.decide(_result(RetrievalAvailability.AVAILABLE))
    invalid = gate.decide(
        _result(RetrievalAvailability.AVAILABLE, [_chunk(metadata={})])
    )

    assert unavailable.availability == EvidenceAvailability.UNAVAILABLE
    assert unavailable.sufficiency == EvidenceSufficiency.NOT_EVALUATED
    assert unavailable.evaluation_confidence == EvaluationConfidence.NOT_SCORABLE
    assert empty.availability == EvidenceAvailability.AVAILABLE
    assert empty.sufficiency == EvidenceSufficiency.EMPTY
    assert invalid.availability == EvidenceAvailability.DEGRADED
    assert invalid.sufficiency == EvidenceSufficiency.INSUFFICIENT


def test_retrieval_gate_accepts_hash_and_single_corpus_binding():
    decision = RetrievalEvidenceGate().decide(
        _result(RetrievalAvailability.AVAILABLE, [_chunk()])
    )

    assert decision.availability == EvidenceAvailability.AVAILABLE
    assert decision.sufficiency == EvidenceSufficiency.NOT_EVALUATED
    assert decision.evaluation_confidence == EvaluationConfidence.NOT_SCORABLE


def test_retrieval_gate_rejects_only_explicit_hard_negative_selection_risk():
    boundary_evidence = _chunk(
        metadata={
            "content_sha256": "a" * 64,
            "corpus_manifest_sha256": "b" * 64,
            "content_kind": "hard_negative",
        }
    )
    risky_selection = boundary_evidence.model_copy(
        update={
            "metadata": {
                **boundary_evidence.metadata,
                "hard_negative_risk": True,
            }
        }
    )

    accepted = RetrievalEvidenceGate().decide(
        _result(RetrievalAvailability.AVAILABLE, [boundary_evidence])
    )
    rejected = RetrievalEvidenceGate().decide(
        _result(RetrievalAvailability.AVAILABLE, [risky_selection])
    )

    assert accepted.sufficiency == EvidenceSufficiency.NOT_EVALUATED
    assert rejected.sufficiency == EvidenceSufficiency.INSUFFICIENT
    assert rejected.reason_codes == ("hard_negative_risk",)


def test_disabled_retrieval_gate_fails_closed_instead_of_claiming_high_confidence():
    decision = RetrievalEvidenceGate(
        enabled=False,
        version="retrieval-gate-disabled-v1",
    ).decide(_result(RetrievalAvailability.AVAILABLE, [_chunk()]))

    assert decision.availability == EvidenceAvailability.DEGRADED
    assert decision.sufficiency == EvidenceSufficiency.NOT_EVALUATED
    assert decision.evaluation_confidence == EvaluationConfidence.NOT_SCORABLE
    assert decision.reason_codes == ("evidence_gate_disabled",)
    assert decision.gate_version == "retrieval-gate-disabled-v1"


def test_candidate_gate_accepts_strong_semantic_and_exact_lexical_agreement():
    candidate = _candidate(
        semantic_score=0.82,
        lexical_score=0.9,
        lexical_rank=1,
        channel_hits=("semantic", "lexical"),
        matched_terms=("redis",),
    )

    decision = RetrievalEvidenceGate().decide_candidates(
        RetrievalAvailability.AVAILABLE,
        [candidate],
        request=_request(),
    )

    assert decision.sufficiency == EvidenceSufficiency.SUFFICIENT
    assert decision.evaluation_confidence == EvaluationConfidence.HIGH
    assert decision.reason_codes == (
        "authoritative_exact_lexical_evidence",
        "semantic_lexical_agreement",
    )


def test_candidate_gate_abstains_on_weak_semantic_without_lexical_evidence():
    decision = RetrievalEvidenceGate().decide_candidates(
        RetrievalAvailability.AVAILABLE,
        [_candidate(semantic_score=0.31)],
        request=_request(),
    )

    assert decision.sufficiency == EvidenceSufficiency.INSUFFICIENT
    assert decision.evaluation_confidence == EvaluationConfidence.NOT_SCORABLE
    assert decision.reason_codes == (
        "minimum_semantic_support_missing",
        "exact_lexical_evidence_missing",
    )


def test_candidate_gate_abstains_on_small_margin_and_domain_mismatch():
    candidates = [
        _candidate(semantic_score=0.70, semantic_rank=1),
        _candidate(
            chunk=_chunk().model_copy(update={"chunk_id": "redis-lock-2"}),
            semantic_score=0.69,
            semantic_rank=2,
        ),
    ]

    decision = RetrievalEvidenceGate().decide_candidates(
        RetrievalAvailability.AVAILABLE,
        candidates,
        request=_request(domain="mysql"),
    )

    assert decision.sufficiency == EvidenceSufficiency.INSUFFICIENT
    assert decision.reason_codes == (
        "small_support_margin",
        "domain_topic_mismatch",
    )


def test_authoritative_exact_lexical_evidence_offsets_weak_semantic_support():
    decision = RetrievalEvidenceGate().decide_candidates(
        RetrievalAvailability.AVAILABLE,
        [
            _candidate(
                semantic_score=0.2,
                lexical_score=0.95,
                lexical_rank=1,
                channel_hits=("lexical",),
                matched_terms=("redis",),
            )
        ],
        request=_request(),
    )

    assert decision.sufficiency == EvidenceSufficiency.SUFFICIENT
    assert decision.evaluation_confidence == EvaluationConfidence.MEDIUM
    assert decision.reason_codes == ("authoritative_exact_lexical_evidence",)


def test_candidate_policy_depends_on_signals_not_query_or_case_identifier():
    candidate = _candidate(semantic_score=0.31)
    first = RetrievalEvidenceGate().decide_candidates(
        RetrievalAvailability.AVAILABLE,
        [candidate],
        request=_request(query="first unrelated wording"),
    )
    second = RetrievalEvidenceGate().decide_candidates(
        RetrievalAvailability.AVAILABLE,
        [candidate],
        request=_request(query="completely different wording"),
    )

    assert first == second


def test_candidate_signals_do_not_invent_two_channel_agreement():
    signals = build_evidence_sufficiency_signals(
        [
            _candidate(
                semantic_score=None,
                lexical_score=0.9,
                lexical_rank=1,
                channel_hits=("lexical",),
                matched_terms=("redis",),
            )
        ],
        request=_request(),
    )

    assert signals.available_channel_count == 1
    assert signals.channel_agreement is False


def test_support_gate_is_task_specific_and_deterministic():
    gate = EvaluationSupportGate()
    base = gate.decide([_chunk()], _unit())
    advanced = gate.decide([_chunk()], _unit(), evaluation_level="advanced")

    assert base.sufficiency == EvidenceSufficiency.SUFFICIENT
    assert base.evaluation_confidence == EvaluationConfidence.HIGH
    assert advanced.sufficiency == EvidenceSufficiency.WEAK
    assert advanced.evaluation_confidence == EvaluationConfidence.LOW
    assert advanced.missing_signals == ("fencing token",)
    assert advanced == gate.decide(
        [_chunk()], _unit(), evaluation_level="advanced"
    )


def test_support_gate_hard_negative_prevents_scorable_decision():
    decision = EvaluationSupportGate().decide(
        [_chunk("owner token atomic compare-and-delete unconditional DEL")],
        _unit(),
    )

    assert decision.sufficiency == EvidenceSufficiency.INSUFFICIENT
    assert decision.evaluation_confidence == EvaluationConfidence.LOW
    assert decision.reason_codes == ("hard_negative_risk",)


def test_support_gate_rejects_unreviewed_unit_and_task_mismatch():
    draft = _unit().model_copy(update={"review_status": "draft"})
    unreviewed = EvaluationSupportGate().decide([_chunk()], draft)
    mismatch = EvaluationSupportGate().decide(
        [_chunk().model_copy(update={"domain": "mysql", "tags": ["mysql"]})],
        _unit(),
    )

    assert unreviewed.sufficiency == EvidenceSufficiency.NOT_EVALUATED
    assert unreviewed.evaluation_confidence == EvaluationConfidence.NOT_SCORABLE
    assert unreviewed.reason_codes == ("knowledge_unit_not_reviewed",)
    assert mismatch.sufficiency == EvidenceSufficiency.INSUFFICIENT
    assert mismatch.evaluation_confidence == EvaluationConfidence.NOT_SCORABLE
    assert mismatch.reason_codes == ("evidence_task_mismatch",)


def test_support_gate_filters_explicitly_untrusted_authority():
    untrusted = _chunk().model_copy(
        update={
            "metadata": {
                **_chunk().metadata,
                "authority_metadata": {"status": "untrusted"},
            }
        }
    )

    decision = EvaluationSupportGate().decide([untrusted], _unit())

    assert decision.sufficiency == EvidenceSufficiency.INSUFFICIENT
    assert decision.evaluation_confidence == EvaluationConfidence.NOT_SCORABLE
    assert decision.reason_codes == ("evidence_authority_unverified",)
