from __future__ import annotations

import math

from pydantic import BaseModel, Field

from app.services.knowledge_eval_dataset import KnowledgeRetrievalDataset


class KnowledgeRetrievalObservation(BaseModel):
    case_id: str
    retrieved_ids: list[str] = Field(default_factory=list)
    bound_evidence_ids: list[str] = Field(default_factory=list)
    reused_evidence_ids: list[str] = Field(default_factory=list)
    latency_ms: float = Field(ge=0)


class KnowledgeRetrievalMetrics(BaseModel):
    passed: bool
    hit_rate_at_3: float
    mean_reciprocal_rank: float
    question_evidence_binding_rate: float
    evidence_continuity_rate: float
    invalid_reference_rate: float
    false_positive_rate: float
    p95_latency_ms: float
    observation_completeness_rate: float
    failed_gates: list[str] = Field(default_factory=list)


def calculate_knowledge_retrieval_metrics(
    dataset: KnowledgeRetrievalDataset,
    observations: list[KnowledgeRetrievalObservation],
) -> KnowledgeRetrievalMetrics:
    observation_lookup = {observation.case_id: observation for observation in observations}
    positive_cases = [case for case in dataset.cases if case.category != "negative"]
    negative_cases = [case for case in dataset.cases if case.category == "negative"]

    hits = 0
    reciprocal_rank_total = 0.0
    bound_positive_cases = 0
    all_bound_ids: list[str] = []
    all_reused_ids: list[str] = []
    invalid_bound_ids = 0
    false_positives = 0
    latencies: list[float] = []

    for case in dataset.cases:
        observation = observation_lookup.get(case.case_id)
        if observation is None:
            continue
        latencies.append(observation.latency_ms)
        retrieved = observation.retrieved_ids
        relevant = set(case.relevant_chunk_ids)
        if case.category == "negative":
            if retrieved:
                false_positives += 1
            continue

        if relevant.intersection(retrieved[:3]):
            hits += 1
        rank = next(
            (index for index, chunk_id in enumerate(retrieved, start=1) if chunk_id in relevant),
            None,
        )
        if rank is not None:
            reciprocal_rank_total += 1.0 / rank
        if observation.bound_evidence_ids:
            bound_positive_cases += 1
        all_bound_ids.extend(observation.bound_evidence_ids)
        all_reused_ids.extend(observation.reused_evidence_ids)
        retrieved_set = set(retrieved)
        invalid_bound_ids += sum(
            chunk_id not in retrieved_set for chunk_id in observation.bound_evidence_ids
        )

    positive_count = len(positive_cases)
    negative_count = len(negative_cases)
    hit_rate = hits / positive_count if positive_count else 0.0
    mrr = reciprocal_rank_total / positive_count if positive_count else 0.0
    binding_rate = bound_positive_cases / positive_count if positive_count else 0.0
    reused_set = set(all_reused_ids)
    continuity_rate = (
        sum(chunk_id in reused_set for chunk_id in all_bound_ids) / len(all_bound_ids)
        if all_bound_ids
        else 0.0
    )
    invalid_rate = (
        invalid_bound_ids / len(all_bound_ids) if all_bound_ids else 0.0
    )
    false_positive_rate = (
        false_positives / negative_count if negative_count else 0.0
    )
    completeness = len(observation_lookup.keys() & {case.case_id for case in dataset.cases}) / len(
        dataset.cases
    )
    p95 = _percentile_95(latencies)

    gate_values = {
        "hit_rate_at_3": hit_rate >= 0.90,
        "mean_reciprocal_rank": mrr >= 0.75,
        "question_evidence_binding_rate": binding_rate == 1.0,
        "evidence_continuity_rate": continuity_rate == 1.0,
        "invalid_reference_rate": invalid_rate == 0.0,
        "false_positive_rate": false_positive_rate <= 0.20,
        "p95_latency_ms": p95 <= 1500,
        "observation_completeness_rate": completeness == 1.0,
    }
    failed_gates = [name for name, passed in gate_values.items() if not passed]
    return KnowledgeRetrievalMetrics(
        passed=not failed_gates,
        hit_rate_at_3=hit_rate,
        mean_reciprocal_rank=mrr,
        question_evidence_binding_rate=binding_rate,
        evidence_continuity_rate=continuity_rate,
        invalid_reference_rate=invalid_rate,
        false_positive_rate=false_positive_rate,
        p95_latency_ms=p95,
        observation_completeness_rate=completeness,
        failed_gates=failed_gates,
    )


def _percentile_95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, math.ceil(0.95 * len(ordered)) - 1)
    return ordered[index]
