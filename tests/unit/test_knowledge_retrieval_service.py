from app.application.knowledge import KnowledgeRetrievalService
from app.domain.knowledge.models import KnowledgeChunk
from app.domain.knowledge.retrieval import (
    ResolvedRetrievalProfile,
    RetrievalAvailability,
    RetrievalCandidate,
    RetrievalChannelResult,
    RetrievalChannelTrace,
    RetrievalIntent,
    RetrievalRequest,
    RetrievalRoutingHints,
)


def _chunk(chunk_id: str, score: float, title: str = "Redis consistency"):
    return KnowledgeChunk(
        chunk_id=chunk_id,
        title=title,
        content="content",
        source_type="theory",
        domain="redis",
        tags=["redis"],
        metadata={
            "content_sha256": chunk_id[0] * 64,
            "corpus_manifest_sha256": "f" * 64,
        },
        score=score,
    )


class SemanticRetriever:
    def __init__(self, availability=RetrievalAvailability.AVAILABLE):
        self.availability = availability
        self.calls = []

    def retrieve_semantic(self, request, *, candidate_limit):
        self.calls.append((request, candidate_limit))
        chunks = (
            [_chunk("alpha", 0.7), _chunk("beta", 0.8)]
            if self.availability != RetrievalAvailability.UNAVAILABLE
            else []
        )
        return RetrievalChannelResult(
            availability=self.availability,
            candidates=[
                RetrievalCandidate(
                    chunk=chunk,
                    semantic_score=chunk.score,
                    semantic_rank=rank,
                    channel_hits=["semantic"],
                )
                for rank, chunk in enumerate(chunks, 1)
            ],
            trace=RetrievalChannelTrace(
                channel="semantic",
                status="completed",
                latency_ms=2,
                candidate_count=2,
                hit_ids=["alpha", "beta"],
            ),
        )


def test_compatibility_service_owns_reranking_and_returns_trace_with_result():
    retriever = SemanticRetriever()
    service = KnowledgeRetrievalService(retriever)
    request = RetrievalRequest(
        query_text="Redis consistency",
        intent=RetrievalIntent.PREP,
        routing_hints=RetrievalRoutingHints(canonical_tags=("redis",)),
        profile_id="legacy",
    )
    profile = ResolvedRetrievalProfile(
        profile_id="legacy",
        profile_version="v1",
        semantic_candidate_limit=12,
        evidence_limit=1,
        minimum_score=0.45,
    )

    result = service.retrieve(request, profile)

    assert retriever.calls == [(request, 12)]
    assert [item.chunk_id for item in result.selected_evidence] == ["beta"]
    assert result.trace.selected_evidence_ids == ["beta"]
    assert result.trace.channels[0].hit_ids == ["alpha", "beta"]
    assert result.retrieval_engine_version == "compatibility-v1"
    assert result.candidates[1].rerank_rank == 1
    assert result.trace.trace_schema_version == "retrieval-trace-v2"
    assert result.trace.intent == RetrievalIntent.PREP
    assert result.trace.sanitized_query_facts.character_count == len(
        request.query_text
    )
    assert request.query_text not in result.trace.model_dump_json()
    assert result.trace.rerank_summary.input_candidate_count == 2
    assert result.evidence_decision == result.trace.evidence_decision
    assert result.evidence_decision.availability.value == "available"
    assert result.evidence_decision.sufficiency.value == "not_evaluated"
    assert result.evidence_decision.evaluation_confidence.value == "not_scorable"
    assert result.trace.resolved_profile == profile
    assert result.trace.selected_evidence_hashes == {"beta": "b" * 64}
    assert result.trace.component_versions.retrieval_engine_version == (
        "compatibility-v1"
    )


def test_service_preserves_unavailable_status_without_fabricating_empty():
    service = KnowledgeRetrievalService(
        SemanticRetriever(RetrievalAvailability.UNAVAILABLE)
    )
    request = RetrievalRequest(
        query_text="Redis consistency",
        intent=RetrievalIntent.QUESTION_REVIEW,
        profile_id="review",
    )
    profile = ResolvedRetrievalProfile(
        profile_id="review",
        profile_version="v1",
    )

    result = service.retrieve(request, profile)

    assert result.availability == RetrievalAvailability.UNAVAILABLE
    assert result.selected_evidence == []
    assert result.evidence_decision == result.trace.evidence_decision
    assert result.evidence_decision.availability.value == "unavailable"
    assert result.evidence_decision.sufficiency.value == "not_evaluated"
    assert result.evidence_decision.evaluation_confidence.value == "not_scorable"


def test_trace_keeps_corpus_identity_even_when_retrieval_has_no_hits():
    retriever = SemanticRetriever()
    original = retriever.retrieve_semantic

    def empty_with_identity(request, *, candidate_limit):
        result = original(request, candidate_limit=candidate_limit)
        return result.model_copy(
            update={
                "candidates": [],
                "corpus_version": "corpus-v2",
                "corpus_manifest_sha256": "c" * 64,
            }
        )

    retriever.retrieve_semantic = empty_with_identity
    request = RetrievalRequest(
        query_text="Redis no hit",
        intent=RetrievalIntent.PREP,
        profile_id="legacy",
    )
    result = KnowledgeRetrievalService(retriever).retrieve(
        request,
        ResolvedRetrievalProfile(profile_id="legacy", profile_version="v1"),
    )

    assert result.selected_evidence == []
    assert result.trace.component_versions.corpus_version == "corpus-v2"
    assert result.trace.component_versions.corpus_manifest_sha256 == "c" * 64
