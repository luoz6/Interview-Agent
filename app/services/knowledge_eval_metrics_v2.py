from __future__ import annotations

import math

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.services.knowledge_eval_dataset_v2 import (
    KnowledgeRetrievalCaseV2,
    KnowledgeRetrievalDatasetV2,
)


class RetrievedKnowledgeItemV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chunk_id: str = Field(min_length=1)
    domain: str = Field(min_length=1)
    source_type: str = Field(min_length=1)
    tags: list[str]


class KnowledgeRetrievalObservationV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(min_length=1)
    retrieved: list[RetrievedKnowledgeItemV2] = Field(default_factory=list)
    bound_evidence_ids: list[str] = Field(default_factory=list)
    replayed_evidence_ids: list[str] = Field(default_factory=list)
    latency_ms: float = Field(ge=0)

    @model_validator(mode="after")
    def validate_unique_ids(self):
        retrieved_ids = [item.chunk_id for item in self.retrieved]
        for field_name, values in (
            ("retrieved", retrieved_ids),
            ("bound_evidence_ids", self.bound_evidence_ids),
            ("replayed_evidence_ids", self.replayed_evidence_ids),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"{field_name} cannot contain duplicate chunk IDs")
        return self


class KnowledgeRetrievalMetricsV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    passed: bool
    recall_at_5: float
    mrr_at_5: float
    ndcg_at_5: float
    filter_correctness_rate: float
    excluded_chunk_violation_rate: float
    vector_validity_rate: float
    evidence_replay_stability_rate: float
    observation_completeness_rate: float
    p95_latency_ms: float
    failed_gates: list[str] = Field(default_factory=list)


def calculate_knowledge_retrieval_metrics_v2(
    dataset: KnowledgeRetrievalDatasetV2,
    observations: list[KnowledgeRetrievalObservationV2],
    *,
    vector_validity_rate: float,
) -> KnowledgeRetrievalMetricsV2:
    if not 0.0 <= vector_validity_rate <= 1.0:
        raise ValueError("vector_validity_rate must be between 0 and 1")

    observations_by_case: dict[str, list[KnowledgeRetrievalObservationV2]] = {}
    for observation in observations:
        observations_by_case.setdefault(observation.case_id, []).append(observation)

    recall_total = 0.0
    reciprocal_rank_total = 0.0
    ndcg_total = 0.0
    correct_filter_items = 0
    returned_items = 0
    excluded_case_violations = 0
    stable_replay_cases = 0
    complete_cases = 0
    latencies: list[float] = []

    for case in dataset.cases:
        matches = observations_by_case.get(case.case_id, [])
        if len(matches) != 1:
            continue
        complete_cases += 1
        observation = matches[0]
        latencies.append(observation.latency_ms)
        retrieved = observation.retrieved[:5]
        retrieved_ids = [item.chunk_id for item in retrieved]
        primary = set(case.primary_relevant_chunk_ids)

        recall_total += len(primary.intersection(retrieved_ids)) / len(primary)
        primary_rank = next(
            (rank for rank, chunk_id in enumerate(retrieved_ids, start=1) if chunk_id in primary),
            None,
        )
        if primary_rank is not None:
            reciprocal_rank_total += 1.0 / primary_rank
        ndcg_total += _ndcg_at_5(case, retrieved_ids)

        for item in retrieved:
            returned_items += 1
            if _matches_case_filters(case, item):
                correct_filter_items += 1

        if set(retrieved_ids).intersection(case.excluded_chunk_ids):
            excluded_case_violations += 1

        bound = set(observation.bound_evidence_ids)
        replayed = set(observation.replayed_evidence_ids)
        if bound and bound.issubset(replayed):
            stable_replay_cases += 1

    case_count = len(dataset.cases)
    recall = recall_total / case_count
    mrr = reciprocal_rank_total / case_count
    ndcg = ndcg_total / case_count
    filter_correctness = (
        correct_filter_items / returned_items if returned_items else 1.0
    )
    excluded_violation_rate = excluded_case_violations / case_count
    replay_stability = stable_replay_cases / case_count
    completeness = complete_cases / case_count
    p95_latency = _percentile_95(latencies)

    gates = {
        "recall_at_5": recall >= 0.90,
        "mrr_at_5": mrr >= 0.80,
        "ndcg_at_5": ndcg >= 0.85,
        "filter_correctness_rate": filter_correctness == 1.0,
        "excluded_chunk_violation_rate": excluded_violation_rate == 0.0,
        "vector_validity_rate": vector_validity_rate == 1.0,
        "evidence_replay_stability_rate": replay_stability == 1.0,
        "observation_completeness_rate": completeness == 1.0,
        "p95_latency_ms": p95_latency <= 1500.0,
    }
    failed_gates = [name for name, passed in gates.items() if not passed]
    return KnowledgeRetrievalMetricsV2(
        passed=not failed_gates,
        recall_at_5=recall,
        mrr_at_5=mrr,
        ndcg_at_5=ndcg,
        filter_correctness_rate=filter_correctness,
        excluded_chunk_violation_rate=excluded_violation_rate,
        vector_validity_rate=vector_validity_rate,
        evidence_replay_stability_rate=replay_stability,
        observation_completeness_rate=completeness,
        p95_latency_ms=p95_latency,
        failed_gates=failed_gates,
    )


def _matches_case_filters(
    case: KnowledgeRetrievalCaseV2, item: RetrievedKnowledgeItemV2
) -> bool:
    return (
        item.domain in case.allowed_domains
        and item.source_type in case.source_types
        and bool(set(item.tags).intersection(case.canonical_tags))
    )


def _ndcg_at_5(case: KnowledgeRetrievalCaseV2, retrieved_ids: list[str]) -> float:
    primary = set(case.primary_relevant_chunk_ids)
    accepted = set(case.accepted_related_chunk_ids)

    def relevance(chunk_id: str) -> int:
        if chunk_id in primary:
            return 3
        if chunk_id in accepted:
            return 1
        return 0

    dcg = sum(
        relevance(chunk_id) / math.log2(rank + 1)
        for rank, chunk_id in enumerate(retrieved_ids[:5], start=1)
    )
    ideal_relevances = sorted(
        [3] * len(primary) + [1] * len(accepted), reverse=True
    )[:5]
    idcg = sum(
        value / math.log2(rank + 1)
        for rank, value in enumerate(ideal_relevances, start=1)
    )
    return dcg / idcg if idcg else 0.0


def _percentile_95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, math.ceil(0.95 * len(ordered)) - 1)
    return ordered[index]
