import math

import pytest

from app.services.knowledge_eval_dataset_v2 import (
    KnowledgeRetrievalCaseV2,
    KnowledgeRetrievalDatasetV2,
)
from app.services.knowledge_eval_metrics_v2 import (
    KnowledgeRetrievalObservationV2,
    RetrievedKnowledgeItemV2,
    calculate_knowledge_retrieval_metrics_v2,
)


def make_dataset() -> KnowledgeRetrievalDatasetV2:
    return KnowledgeRetrievalDatasetV2(
        version="v2-test",
        cases=[
            KnowledgeRetrievalCaseV2(
                case_id="redis-first",
                evaluation_group="redis",
                query_text="缓存一致性怎样处理？",
                canonical_tags=["redis"],
                source_types=["theory"],
                allowed_domains=["redis"],
                primary_relevant_chunk_ids=["primary-a"],
                accepted_related_chunk_ids=["related-a"],
                excluded_chunk_ids=["excluded-a"],
            ),
            KnowledgeRetrievalCaseV2(
                case_id="mysql-second",
                evaluation_group="relational-database",
                query_text="联合索引怎样减少回表？",
                canonical_tags=["mysql"],
                source_types=["theory"],
                allowed_domains=["mysql"],
                primary_relevant_chunk_ids=["primary-b"],
                accepted_related_chunk_ids=["related-b"],
                excluded_chunk_ids=["excluded-b"],
            ),
            KnowledgeRetrievalCaseV2(
                case_id="rocketmq-miss",
                evaluation_group="rocketmq",
                query_text="消息重复消费怎样处理？",
                canonical_tags=["rocketmq"],
                source_types=["theory"],
                allowed_domains=["rocketmq"],
                primary_relevant_chunk_ids=["primary-c"],
                excluded_chunk_ids=["excluded-c"],
            ),
        ],
    )


def item(chunk_id: str, domain: str, tag: str) -> RetrievedKnowledgeItemV2:
    return RetrievedKnowledgeItemV2(
        chunk_id=chunk_id,
        domain=domain,
        source_type="theory",
        tags=[tag],
    )


def make_observations() -> list[KnowledgeRetrievalObservationV2]:
    return [
        KnowledgeRetrievalObservationV2(
            case_id="redis-first",
            retrieved=[
                item("primary-a", "redis", "redis"),
                item("related-a", "redis", "redis"),
            ],
            bound_evidence_ids=["primary-a"],
            replayed_evidence_ids=["primary-a"],
            latency_ms=10,
        ),
        KnowledgeRetrievalObservationV2(
            case_id="mysql-second",
            retrieved=[
                item("related-b", "mysql", "mysql"),
                item("primary-b", "mysql", "mysql"),
            ],
            bound_evidence_ids=["related-b"],
            replayed_evidence_ids=["related-b"],
            latency_ms=20,
        ),
        KnowledgeRetrievalObservationV2(
            case_id="rocketmq-miss",
            retrieved=[item("other", "rocketmq", "rocketmq")],
            bound_evidence_ids=["other"],
            replayed_evidence_ids=["other"],
            latency_ms=30,
        ),
    ]


def test_v2_metrics_use_graded_relevance_and_top_five():
    metrics = calculate_knowledge_retrieval_metrics_v2(
        dataset=make_dataset(),
        observations=make_observations(),
        vector_validity_rate=1.0,
    )

    ideal = 3 + 1 / math.log2(3)
    second_case_dcg = 1 + 3 / math.log2(3)
    expected_ndcg = (1.0 + second_case_dcg / ideal + 0.0) / 3
    assert metrics.recall_at_5 == pytest.approx(2 / 3)
    assert metrics.mrr_at_5 == pytest.approx(0.5)
    assert metrics.ndcg_at_5 == pytest.approx(expected_ndcg)
    assert metrics.filter_correctness_rate == 1.0
    assert metrics.excluded_chunk_violation_rate == 0.0
    assert metrics.evidence_replay_stability_rate == 1.0
    assert metrics.observation_completeness_rate == 1.0


@pytest.mark.parametrize(
    ("mutation", "failed_gate"),
    [
        ("wrong-domain", "filter_correctness_rate"),
        ("wrong-source", "filter_correctness_rate"),
        ("missing-tag", "filter_correctness_rate"),
        ("excluded", "excluded_chunk_violation_rate"),
        ("replay", "evidence_replay_stability_rate"),
        ("latency", "p95_latency_ms"),
    ],
)
def test_v2_metrics_fail_closed_for_filter_exclusion_replay_and_latency(
    mutation: str, failed_gate: str
):
    observations = make_observations()
    first = observations[0]
    if mutation == "wrong-domain":
        first.retrieved[0].domain = "mysql"
    elif mutation == "wrong-source":
        first.retrieved[0].source_type = "engineering_guide"
    elif mutation == "missing-tag":
        first.retrieved[0].tags = ["cache"]
    elif mutation == "excluded":
        first.retrieved.append(item("excluded-a", "redis", "redis"))
    elif mutation == "replay":
        first.replayed_evidence_ids = []
    else:
        first.latency_ms = 1501

    metrics = calculate_knowledge_retrieval_metrics_v2(
        make_dataset(), observations, vector_validity_rate=1.0
    )

    assert metrics.passed is False
    assert failed_gate in metrics.failed_gates


def test_v2_metrics_fail_closed_for_missing_observation_and_invalid_vectors():
    metrics = calculate_knowledge_retrieval_metrics_v2(
        make_dataset(), make_observations()[:-1], vector_validity_rate=0.99
    )

    assert metrics.observation_completeness_rate == pytest.approx(2 / 3)
    assert metrics.vector_validity_rate == 0.99
    assert "observation_completeness_rate" in metrics.failed_gates
    assert "vector_validity_rate" in metrics.failed_gates


def test_v2_observation_rejects_duplicate_retrieved_ids():
    duplicate = item("same", "redis", "redis")

    with pytest.raises(ValueError, match="duplicate chunk IDs"):
        KnowledgeRetrievalObservationV2(
            case_id="duplicate",
            retrieved=[duplicate, duplicate],
            latency_ms=1,
        )


def test_v2_metrics_treat_duplicate_case_observations_as_incomplete():
    observations = make_observations()
    observations.append(observations[0].model_copy(deep=True))

    metrics = calculate_knowledge_retrieval_metrics_v2(
        make_dataset(), observations, vector_validity_rate=1.0
    )

    assert metrics.observation_completeness_rate == pytest.approx(2 / 3)
    assert "observation_completeness_rate" in metrics.failed_gates
