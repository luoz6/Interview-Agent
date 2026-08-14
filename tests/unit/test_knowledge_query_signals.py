from app.domain.knowledge.models import KnowledgeChunk
from app.domain.knowledge.query_signals import QuerySignalAnalyzer
from app.domain.knowledge.retrieval import RetrievalCandidate


def _candidate(
    *,
    matched_terms=(),
    aliases=(),
    technical_terms=(),
):
    return RetrievalCandidate(
        chunk=KnowledgeChunk(
            chunk_id="knowledge-1",
            title="分布式系统",
            content="安全诊断摘要",
            source_type="engineering_guide",
            domain="backend",
            tags=["backend"],
            metadata={
                "aliases": list(aliases),
                "technical_terms": list(technical_terms),
                "content_sha256": "a" * 64,
                "corpus_manifest_sha256": "b" * 64,
            },
            score=0.8,
        ),
        lexical_score=0.9,
        lexical_rank=1,
        channel_hits=["lexical"],
        matched_terms=list(matched_terms),
    )


def _decide(query, candidate, *, enabled=True):
    return QuerySignalAnalyzer().decide(
        query,
        semantic_candidates=(),
        lexical_candidates=(candidate,),
        base_semantic_weight=1.0,
        base_lexical_weight=1.0,
        enabled=enabled,
        semantic_available=True,
        lexical_available=True,
    )


def test_exact_alias_selects_lexical_dominant_weights():
    decision = _decide(
        "Redis 锁应该如何安全释放？",
        _candidate(matched_terms=("redis",), aliases=("Redis 锁",)),
    )

    assert decision.query_signal == "lexical_dominant"
    assert decision.semantic_weight == 0.8
    assert decision.lexical_weight == 1.4
    assert "exact_alias_match" in decision.reason_codes


def test_acronym_and_technical_term_select_lexical_dominant():
    decision = _decide(
        "MVCC 出现版本链膨胀时怎样诊断？",
        _candidate(matched_terms=("mvcc",), technical_terms=("MVCC",)),
    )

    assert decision.query_signal == "lexical_dominant"
    assert {"exact_technical_term_match", "acronym_signal"} <= set(
        decision.reason_codes
    )


def test_long_chinese_paraphrase_with_weak_keyword_is_semantic_dominant():
    decision = _decide(
        "消息重复到达并导致业务副作用再次发生，请说明成因、控制影响的方法和恢复后的核验方式",
        _candidate(matched_terms=("消息",)),
    )

    assert decision.query_signal == "semantic_dominant"
    assert decision.semantic_weight == 1.3
    assert decision.lexical_weight == 0.7
    assert decision.reason_codes == (
        "long_cjk_paraphrase",
        "weak_exact_term_support",
    )


def test_mixed_short_query_stays_balanced_and_is_deterministic():
    candidate = _candidate(matched_terms=("一致性",))
    first = _decide("缓存一致性怎么分析", candidate)
    second = _decide("缓存一致性怎么分析", candidate)

    assert first.query_signal == "balanced"
    assert first.semantic_weight == 1.0
    assert first.lexical_weight == 1.0
    assert first == second


def test_disabled_profile_preserves_fixed_weights_without_query_content():
    decision = _decide(
        "这段原文绝不能出现在决策对象中",
        _candidate(aliases=("原文",)),
        enabled=False,
    )

    assert decision.model_dump() == {
        "query_signal": "balanced",
        "semantic_weight": 1.0,
        "lexical_weight": 1.0,
        "reason_codes": ("query_aware_fusion_disabled",),
    }
    assert "这段原文" not in str(decision.model_dump())


def test_unavailable_channel_is_not_reported_as_two_channel_agreement():
    decision = QuerySignalAnalyzer().decide(
        "Redis Lua",
        semantic_candidates=(),
        lexical_candidates=(_candidate(matched_terms=("redis", "lua")),),
        base_semantic_weight=1.0,
        base_lexical_weight=1.0,
        enabled=True,
        semantic_available=False,
        lexical_available=True,
    )

    assert decision.query_signal == "lexical_dominant"
    assert decision.reason_codes == ("semantic_channel_unavailable",)
