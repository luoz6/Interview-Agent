import pytest

from app.services.knowledge_eval_dataset_v3 import (
    KnowledgeRetrievalCaseV3,
    KnowledgeRetrievalDatasetV3,
)
from app.services.knowledge_eval_metrics_v2 import RetrievedKnowledgeItemV2
from app.services.knowledge_eval_metrics_v3 import (
    KnowledgeRetrievalObservationV3,
    calculate_knowledge_retrieval_metrics_v3,
    compare_knowledge_retrieval_metrics_v3,
)


def _case(**overrides):
    payload = {
        "case_id": "redis-lock",
        "case_type": "exact_technical_term",
        "split": "holdout",
        "evaluation_group": "redis",
        "query_text": "Redis 分布式锁怎样安全释放？",
        "canonical_tags": ["redis"],
        "source_types": ["theory"],
        "allowed_domains": ["redis"],
        "primary_relevant_chunk_ids": ["lock"],
        "accepted_related_chunk_ids": [],
        "excluded_chunk_ids": ["cache"],
    }
    payload.update(overrides)
    return KnowledgeRetrievalCaseV3(**payload)


def _dataset():
    return KnowledgeRetrievalDatasetV3(
        version="v3-test",
        corpus_manifest_sha256="a" * 64,
        cases=[
            _case(split="tuning", case_id="tuning-lock", query_text="锁如何释放？"),
            _case(),
            _case(
                case_id="hard-negative",
                case_type="hard_negative",
                query_text="缓存淘汰是否等同于释放分布式锁？",
            ),
            _case(
                case_id="no-evidence",
                case_type="no_evidence",
                query_text="不存在的缓存一致性协议是什么？",
                primary_relevant_chunk_ids=[],
                accepted_related_chunk_ids=[],
                expected_no_evidence=True,
            ),
        ],
    )


def _item(chunk_id: str):
    return RetrievedKnowledgeItemV2(
        chunk_id=chunk_id,
        domain="redis",
        source_type="theory",
        tags=["redis"],
    )


def _observations(engine="legacy"):
    return [
        KnowledgeRetrievalObservationV3(
            case_id="redis-lock",
            engine_version=engine,
            retrieved=[_item("lock")],
            bound_evidence_ids=["lock"],
            replayed_evidence_ids=["lock"],
            semantic_hit_ids=["lock"],
            latency_ms=10,
        ),
        KnowledgeRetrievalObservationV3(
            case_id="hard-negative",
            engine_version=engine,
            retrieved=[_item("lock")],
            bound_evidence_ids=["lock"],
            replayed_evidence_ids=["lock"],
            lexical_hit_ids=["lock"],
            latency_ms=20,
        ),
        KnowledgeRetrievalObservationV3(
            case_id="no-evidence",
            engine_version=engine,
            declared_no_evidence=True,
            latency_ms=5,
        ),
    ]


def test_v3_metrics_reuse_v2_ranking_metrics_and_add_no_evidence_channels():
    metrics = calculate_knowledge_retrieval_metrics_v3(
        _dataset(), _observations(), split="holdout"
    )

    assert metrics.observation_completeness_rate == 1.0
    assert metrics.recall_at_5 == 1.0
    assert metrics.mrr_at_5 == 1.0
    assert metrics.ndcg_at_5 == 1.0
    assert metrics.hit_at_1 == 1.0
    assert metrics.filter_correctness_rate == 1.0
    assert metrics.vector_validity_rate == 1.0
    assert metrics.no_evidence_precision == 1.0
    assert metrics.no_evidence_recall == 1.0
    assert metrics.no_evidence_f1 == 1.0
    assert metrics.evidence_precision_at_5 == 1.0
    assert metrics.domain_routing_accuracy == 1.0
    assert metrics.topic_routing_accuracy == 1.0
    assert metrics.cross_channel_contribution_rate == 1.0
    assert metrics.semantic_only_win_rate == 0.5
    assert metrics.lexical_only_win_rate == 0.5
    assert metrics.hybrid_win_rate == 0.0
    no_evidence = metrics.case_type_breakdown["no_evidence"]
    assert no_evidence["case_count"] == 1
    assert no_evidence["observation_completeness_rate"] == 1.0
    assert no_evidence["hit_at_1"] == 0.0
    assert no_evidence["no_evidence_precision"] == 1.0
    assert no_evidence["no_evidence_recall"] == 1.0
    assert metrics.case_type_breakdown["exact_technical_term"]["ndcg_at_5"] == 1.0


def test_v3_metrics_reject_mixed_engine_versions():
    observations = _observations()
    observations[1].engine_version = "hybrid"

    with pytest.raises(ValueError, match="mix engine"):
        calculate_knowledge_retrieval_metrics_v3(
            _dataset(), observations, split="holdout"
        )


def test_v3_observation_rejects_evidence_binding_for_declared_empty():
    with pytest.raises(ValueError, match="cannot bind"):
        KnowledgeRetrievalObservationV3(
            case_id="empty",
            engine_version="legacy",
            declared_no_evidence=True,
            bound_evidence_ids=["invented"],
            latency_ms=1,
        )


def test_paired_comparison_reports_candidate_minus_baseline():
    baseline = calculate_knowledge_retrieval_metrics_v3(
        _dataset(), _observations("legacy"), split="holdout"
    )
    candidate_observations = _observations("hybrid")
    candidate_observations[0].retrieved = [_item("cache"), _item("lock")]
    candidate = calculate_knowledge_retrieval_metrics_v3(
        _dataset(), candidate_observations, split="holdout"
    )

    comparison = compare_knowledge_retrieval_metrics_v3(baseline, candidate)
    hit_at_1 = next(item for item in comparison.metrics if item.metric == "hit_at_1")
    assert hit_at_1.baseline == 1.0
    assert hit_at_1.candidate == 0.5
    assert hit_at_1.delta == -0.5
    assert (
        comparison.case_type_deltas["exact_technical_term"]["hit_at_1"]
        == -1.0
    )
