import pytest

from app.services.knowledge_eval_dataset import (
    KnowledgeRetrievalCase,
    KnowledgeRetrievalDataset,
)
from app.services.knowledge_eval_metrics import (
    KnowledgeRetrievalObservation,
    calculate_knowledge_retrieval_metrics,
)


def make_dataset() -> KnowledgeRetrievalDataset:
    return KnowledgeRetrievalDataset(
        version="test",
        cases=[
            KnowledgeRetrievalCase(
                case_id="hit-first",
                category="relevant",
                domain="redis",
                query_text="redis consistency",
                canonical_tags=["redis"],
                relevant_chunk_ids=["redis_consistency"],
            ),
            KnowledgeRetrievalCase(
                case_id="hit-second",
                category="weak_keyword",
                domain="mysql",
                query_text="large table change",
                canonical_tags=["mysql"],
                relevant_chunk_ids=["mysql_online_migration"],
            ),
            KnowledgeRetrievalCase(
                case_id="negative",
                category="negative",
                domain="kafka",
                query_text="hotel itinerary",
                canonical_tags=["kafka"],
            ),
        ],
    )


def test_metrics_calculate_ranking_continuity_and_latency():
    observations = [
        KnowledgeRetrievalObservation(
            case_id="hit-first",
            retrieved_ids=["redis_consistency", "redis_backend"],
            bound_evidence_ids=["redis_consistency"],
            reused_evidence_ids=["redis_consistency"],
            latency_ms=10,
        ),
        KnowledgeRetrievalObservation(
            case_id="hit-second",
            retrieved_ids=["mysql_deadlocks", "mysql_online_migration"],
            bound_evidence_ids=["mysql_deadlocks"],
            reused_evidence_ids=["mysql_deadlocks"],
            latency_ms=20,
        ),
        KnowledgeRetrievalObservation(
            case_id="negative",
            retrieved_ids=[],
            latency_ms=30,
        ),
    ]

    metrics = calculate_knowledge_retrieval_metrics(make_dataset(), observations)

    assert metrics.hit_rate_at_3 == 1.0
    assert metrics.mean_reciprocal_rank == pytest.approx(0.75)
    assert metrics.question_evidence_binding_rate == 1.0
    assert metrics.evidence_continuity_rate == 1.0
    assert metrics.invalid_reference_rate == 0.0
    assert metrics.false_positive_rate == 0.0
    assert metrics.p95_latency_ms == 30
    assert metrics.passed is True


def test_metrics_fail_closed_for_missing_observations_and_invalid_bindings():
    observations = [
        KnowledgeRetrievalObservation(
            case_id="hit-first",
            retrieved_ids=["wrong"],
            bound_evidence_ids=["invented"],
            reused_evidence_ids=[],
            latency_ms=2000,
        ),
        KnowledgeRetrievalObservation(
            case_id="negative",
            retrieved_ids=["kafka_delivery"],
            latency_ms=5,
        ),
    ]

    metrics = calculate_knowledge_retrieval_metrics(make_dataset(), observations)

    assert metrics.passed is False
    assert metrics.observation_completeness_rate < 1.0
    assert metrics.invalid_reference_rate == 1.0
    assert metrics.evidence_continuity_rate == 0.0
    assert "observation_completeness_rate" in metrics.failed_gates
    assert "false_positive_rate" in metrics.failed_gates
