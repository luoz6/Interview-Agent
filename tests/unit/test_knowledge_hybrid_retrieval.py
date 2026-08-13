from app.adapters.knowledge import ExactTermLexicalRetriever
from app.application.knowledge.hybrid_retrieval_service import (
    HybridKnowledgeRetrievalService,
)
from app.domain.knowledge.fusion import (
    rank_normalized_score_fusion,
    weighted_reciprocal_rank_fusion,
)
from app.domain.knowledge.lexical import extract_technical_terms
from app.domain.knowledge.models import KnowledgeChunk
from app.domain.knowledge.retrieval import (
    ResolvedRetrievalProfile,
    RetrievalAvailability,
    RetrievalCandidate,
    RetrievalChannelResult,
    RetrievalChannelTrace,
    RetrievalHardConstraints,
    RetrievalIntent,
    RetrievalRequest,
    RetrievalRoutingHints,
)
from threading import Event
from time import sleep


def _chunk(chunk_id, title, *, aliases=None, score=0.8):
    return KnowledgeChunk(
        chunk_id=chunk_id,
        title=title,
        content="正文",
        source_type="theory",
        domain="redis",
        tags=["redis"],
        metadata={
            "aliases": aliases or [],
            "content_sha256": chunk_id[0] * 64,
            "corpus_manifest_sha256": "f" * 64,
        },
        score=score,
    )


class Source:
    def __init__(self, chunks=None, fail=False):
        self.chunks = chunks or []
        self.fail = fail

    def load_active_candidates(self, *, source_types=None):
        if self.fail:
            raise RuntimeError("unavailable")
        return list(self.chunks)


class Semantic:
    def __init__(self, candidates, availability=RetrievalAvailability.AVAILABLE):
        self.candidates = candidates
        self.availability = availability

    def retrieve_semantic(self, request, *, candidate_limit):
        return RetrievalChannelResult(
            availability=self.availability,
            candidates=self.candidates[:candidate_limit],
            trace=RetrievalChannelTrace(
                channel="semantic",
                status="completed" if self.candidates else "unavailable",
                latency_ms=1,
                candidate_count=len(self.candidates),
                hit_ids=[item.chunk_id for item in self.candidates],
                reason_code=(
                    "semantic_unavailable"
                    if self.availability == RetrievalAvailability.UNAVAILABLE
                    else None
                ),
            ),
        )


def _semantic_candidate(chunk, rank=1):
    return RetrievalCandidate(
        chunk=chunk,
        semantic_score=chunk.score,
        semantic_rank=rank,
        channel_hits=["semantic"],
    )


def _request(text="如何用 Lua 安全释放 Redis 分布式锁？"):
    return RetrievalRequest(
        query_text=text,
        intent=RetrievalIntent.QUESTION_REVIEW,
        profile_id="hybrid",
    )


def _profile():
    return ResolvedRetrievalProfile(
        profile_id="hybrid",
        profile_version="v1",
        semantic_enabled=True,
        lexical_enabled=True,
        semantic_candidate_limit=10,
        lexical_candidate_limit=10,
        fusion_candidate_limit=10,
        evidence_limit=5,
        minimum_score=0,
    )


def test_technical_term_normalization_preserves_symbol_terms():
    terms = extract_technical_terms("Ｃ＋＋ B+Tree MVCC 与 maxReconsumeTimes")

    assert {"c++", "b+tree", "mvcc", "maxreconsumetimes"} <= terms


def test_exact_term_retriever_matches_alias_without_embedding():
    source = Source(
        [
            _chunk("lock", "分布式锁安全释放", aliases=["Lua compare-and-delete"]),
            _chunk("cache", "缓存击穿"),
        ]
    )
    result = ExactTermLexicalRetriever(source).retrieve_lexical(
        _request(), candidate_limit=5
    )

    assert result.availability == RetrievalAvailability.AVAILABLE
    assert [item.chunk_id for item in result.candidates] == ["lock"]
    assert "lua" in result.candidates[0].matched_terms
    assert result.candidates[0].lexical_rank == 1


def test_weighted_rrf_deduplicates_and_records_channel_contribution():
    shared = _chunk("shared", "MVCC")
    semantic = [_semantic_candidate(shared)]
    lexical = [
        RetrievalCandidate(
            chunk=shared,
            lexical_score=1,
            lexical_rank=1,
            channel_hits=["lexical"],
            matched_terms=["mvcc"],
        )
    ]

    fused = weighted_reciprocal_rank_fusion(
        semantic,
        lexical,
        k=60,
        semantic_weight=1,
        lexical_weight=1,
        limit=5,
    )

    assert len(fused) == 1
    assert fused[0].channel_hits == ["lexical", "semantic"]
    assert fused[0].fusion_score == 2 / 61
    assert fused[0].matched_terms == ["mvcc"]


def test_rank_normalized_score_fusion_is_provider_score_independent():
    first = _chunk("first", "first")
    second = _chunk("second", "second")
    semantic = [
        _semantic_candidate(first, 1),
        _semantic_candidate(second, 2),
    ]
    lexical = [
        RetrievalCandidate(
            chunk=second,
            lexical_score=999,
            lexical_rank=1,
            channel_hits=["lexical"],
        )
    ]

    fused = rank_normalized_score_fusion(
        semantic,
        lexical,
        semantic_weight=1,
        lexical_weight=1,
        limit=5,
    )

    assert [item.chunk_id for item in fused] == ["first", "second"]
    assert fused[0].fusion_score == 1.0
    assert fused[1].fusion_score == 1.0
    assert fused[1].channel_hits == ["lexical", "semantic"]


def test_hybrid_service_applies_rerank_candidate_limit_before_reranking():
    chunks = [
        _chunk(f"chunk-{index}", f"chunk {index}", score=1 - index / 10)
        for index in range(4)
    ]
    seen = []

    class RecordingReranker:
        def rerank_candidates(self, candidates, **kwargs):
            seen.extend(item.chunk_id for item in candidates)
            return [
                item.model_copy(update={"rerank_rank": rank, "rerank_score": 1.0})
                for rank, item in enumerate(candidates[: kwargs["limit"]], 1)
            ]

    service = HybridKnowledgeRetrievalService(
        Semantic([_semantic_candidate(chunk, index + 1) for index, chunk in enumerate(chunks)]),
        ExactTermLexicalRetriever(Source([])),
        reranker=RecordingReranker(),
    )
    profile = _profile().model_copy(
        update={"rerank_candidate_limit": 2, "evidence_limit": 2}
    )

    service.retrieve(_request(), profile)
    service.close()

    assert seen == ["chunk-0", "chunk-1"]


def test_routing_hints_are_soft_but_explicit_filters_are_hard_for_lexical():
    redis = _chunk("redis", "Lua lock", aliases=["Lua"])
    mysql = KnowledgeChunk(
        chunk_id="mysql",
        title="Lua transaction",
        content="正文",
        source_type="theory",
        domain="mysql",
        tags=["mysql"],
        metadata={"aliases": ["Lua"]},
        score=0.8,
    )
    retriever = ExactTermLexicalRetriever(Source([redis, mysql]))
    soft = _request().model_copy(
        update={
            "routing_hints": RetrievalRoutingHints(
                domains=("redis",),
                canonical_tags=("redis",),
            )
        }
    )
    hard = soft.model_copy(
        update={
            "hard_constraints": RetrievalHardConstraints(
                filters={"domains": ("redis",)}
            )
        }
    )

    assert {item.chunk_id for item in retriever.retrieve_lexical(
        soft, candidate_limit=5
    ).candidates} == {"redis", "mysql"}
    assert [item.chunk_id for item in retriever.retrieve_lexical(
        hard, candidate_limit=5
    ).candidates] == ["redis"]


def test_hybrid_service_keeps_lexical_only_win_and_channel_trace():
    semantic_chunk = _chunk("cache", "缓存一致性", score=0.7)
    lexical_chunk = _chunk(
        "lock", "分布式锁", aliases=["Lua compare-and-delete"], score=0.6
    )
    service = HybridKnowledgeRetrievalService(
        Semantic([_semantic_candidate(semantic_chunk)]),
        ExactTermLexicalRetriever(Source([semantic_chunk, lexical_chunk])),
    )

    result = service.retrieve(_request(), _profile())

    assert result.availability == RetrievalAvailability.AVAILABLE
    assert {item.chunk_id for item in result.candidates} == {"cache", "lock"}
    lock = next(item for item in result.candidates if item.chunk_id == "lock")
    assert lock.channel_hits == ["lexical"]
    assert [item.channel for item in result.trace.channels] == ["semantic", "lexical"]
    assert result.trace.fusion_summary.strategy == "weighted_rrf"
    assert result.trace.fusion_summary.fused_candidate_count == 2
    assert result.trace.rerank_summary.input_candidate_count == 2
    assert result.trace.latency_breakdown_ms["total"] == result.latency_ms


def test_hybrid_service_degrades_to_lexical_when_semantic_is_unavailable():
    lexical_chunk = _chunk("lock", "分布式锁", aliases=["Lua"])
    service = HybridKnowledgeRetrievalService(
        Semantic([], RetrievalAvailability.UNAVAILABLE),
        ExactTermLexicalRetriever(Source([lexical_chunk])),
    )

    result = service.retrieve(_request(), _profile())

    assert result.availability == RetrievalAvailability.DEGRADED
    assert [item.chunk_id for item in result.selected_evidence] == ["lock"]
    assert result.degraded_reasons == ["semantic_unavailable"]


def test_hybrid_service_reports_unavailable_when_all_channels_fail():
    service = HybridKnowledgeRetrievalService(
        Semantic([], RetrievalAvailability.UNAVAILABLE),
        ExactTermLexicalRetriever(Source(fail=True)),
    )

    result = service.retrieve(_request(), _profile())

    assert result.availability == RetrievalAvailability.UNAVAILABLE
    assert result.selected_evidence == []
    assert set(result.degraded_reasons) == {
        "semantic_unavailable",
        "lexical_unavailable",
    }


def test_hybrid_service_times_out_one_channel_and_bounds_inflight_work():
    release = Event()

    class BlockingSemantic:
        def retrieve_semantic(self, request, *, candidate_limit):
            release.wait(timeout=1)
            return Semantic([]).retrieve_semantic(
                request, candidate_limit=candidate_limit
            )

    lexical_chunk = _chunk("lock", "Lua lock", aliases=["Lua"])
    service = HybridKnowledgeRetrievalService(
        BlockingSemantic(),
        ExactTermLexicalRetriever(Source([lexical_chunk])),
    )
    profile = _profile().model_copy(
        update={"semantic_timeout_ms": 5, "lexical_timeout_ms": 100}
    )
    first = service.retrieve(_request(), profile)
    second = service.retrieve(_request(), profile)
    release.set()
    service.close()

    assert "semantic_timeout" in first.degraded_reasons
    assert "semantic_capacity_exhausted" in second.degraded_reasons
    assert first.availability == RetrievalAvailability.DEGRADED
    assert second.availability == RetrievalAvailability.DEGRADED
    assert first.trace.degraded_path_latency_ms == first.latency_ms


def test_hybrid_service_bounds_reranker_and_uses_fused_order_on_timeout():
    release = Event()
    chunks = [
        _chunk("alpha", "alpha", score=0.9),
        _chunk("beta", "beta", score=0.8),
    ]

    class BlockingReranker:
        def rerank_candidates(self, candidates, **kwargs):
            release.wait(timeout=1)
            return list(reversed(candidates))

    service = HybridKnowledgeRetrievalService(
        Semantic([_semantic_candidate(chunk, index + 1) for index, chunk in enumerate(chunks)]),
        ExactTermLexicalRetriever(Source([])),
        reranker=BlockingReranker(),
    )
    profile = _profile().model_copy(
        update={"rerank_timeout_ms": 5, "total_timeout_ms": 100}
    )

    result = service.retrieve(_request("alpha"), profile)
    release.set()
    sleep(0.01)
    service.close()

    assert result.availability == RetrievalAvailability.DEGRADED
    assert "reranker_timeout" in result.degraded_reasons
    assert [item.chunk_id for item in result.selected_evidence] == ["alpha", "beta"]


def test_fusion_order_is_authoritative_when_raw_scores_conflict():
    fusion_first = _chunk("fusion-first", "unrelated one", score=0.55)
    raw_first = _chunk("raw-first", "unrelated two", score=0.99)
    service = HybridKnowledgeRetrievalService(
        Semantic(
            [
                _semantic_candidate(fusion_first, 1),
                _semantic_candidate(raw_first, 2),
            ]
        ),
        ExactTermLexicalRetriever(Source([])),
    )

    result = service.retrieve(_request("neutral query"), _profile())
    service.close()

    assert fusion_first.score < raw_first.score
    assert [item.chunk_id for item in result.selected_evidence[:2]] == [
        "fusion-first",
        "raw-first",
    ]
    candidates = {item.chunk_id: item for item in result.candidates}
    assert candidates["fusion-first"].fusion_rank == 1
    assert candidates["fusion-first"].rerank_rank == 1
    assert candidates["fusion-first"].rerank_score > candidates["raw-first"].rerank_score
