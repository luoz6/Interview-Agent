from __future__ import annotations

from collections import Counter
import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.services.knowledge_eval_dataset_v3 import (
    DatasetSplit,
    KnowledgeRetrievalDatasetV3,
)
from app.services.knowledge_eval_metrics_v2 import (
    KnowledgeRetrievalObservationV2,
    RetrievedKnowledgeItemV2,
    calculate_knowledge_retrieval_metrics_v2,
)


class KnowledgeRetrievalObservationV3(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(min_length=1)
    engine_version: str = Field(min_length=1)
    retrieved: list[RetrievedKnowledgeItemV2] = Field(default_factory=list)
    bound_evidence_ids: list[str] = Field(default_factory=list)
    replayed_evidence_ids: list[str] = Field(default_factory=list)
    semantic_hit_ids: list[str] = Field(default_factory=list)
    lexical_hit_ids: list[str] = Field(default_factory=list)
    declared_no_evidence: bool = False
    latency_ms: float = Field(ge=0)

    @model_validator(mode="after")
    def validate_unique_ids(self):
        for field_name, values in (
            ("retrieved", [item.chunk_id for item in self.retrieved]),
            ("bound_evidence_ids", self.bound_evidence_ids),
            ("replayed_evidence_ids", self.replayed_evidence_ids),
            ("semantic_hit_ids", self.semantic_hit_ids),
            ("lexical_hit_ids", self.lexical_hit_ids),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"{field_name} cannot contain duplicate chunk IDs")
        if self.declared_no_evidence and self.bound_evidence_ids:
            raise ValueError("no-evidence observations cannot bind evidence")
        return self

    def as_v2(self) -> KnowledgeRetrievalObservationV2:
        return KnowledgeRetrievalObservationV2(
            case_id=self.case_id,
            retrieved=self.retrieved,
            bound_evidence_ids=self.bound_evidence_ids,
            replayed_evidence_ids=self.replayed_evidence_ids,
            latency_ms=self.latency_ms,
        )


class KnowledgeRetrievalMetricsV3(BaseModel):
    model_config = ConfigDict(extra="forbid")

    split: DatasetSplit
    engine_version: str
    case_count: int = Field(ge=0)
    observation_completeness_rate: float = Field(ge=0, le=1)
    filter_correctness_rate: float = Field(ge=0, le=1)
    vector_validity_rate: float = Field(ge=0, le=1)
    recall_at_5: float = Field(ge=0, le=1)
    mrr_at_5: float = Field(ge=0, le=1)
    ndcg_at_5: float = Field(ge=0, le=1)
    hit_at_1: float = Field(ge=0, le=1)
    hard_negative_false_positive_rate: float = Field(ge=0, le=1)
    no_evidence_precision: float = Field(ge=0, le=1)
    no_evidence_recall: float = Field(ge=0, le=1)
    no_evidence_f1: float = Field(ge=0, le=1)
    evidence_precision_at_5: float = Field(ge=0, le=1)
    domain_routing_accuracy: float = Field(ge=0, le=1)
    topic_routing_accuracy: float = Field(ge=0, le=1)
    cross_channel_contribution_rate: float = Field(ge=0, le=1)
    evidence_replay_stability_rate: float = Field(ge=0, le=1)
    excluded_chunk_violation_rate: float = Field(ge=0, le=1)
    p95_latency_ms: float = Field(ge=0)
    semantic_only_win_rate: float = Field(ge=0, le=1)
    lexical_only_win_rate: float = Field(ge=0, le=1)
    hybrid_win_rate: float = Field(ge=0, le=1)
    case_type_counts: dict[str, int]
    case_type_breakdown: dict[str, dict[str, float | int]]


class PairedMetricDelta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metric: str
    baseline: float
    candidate: float
    delta: float


class KnowledgeRetrievalComparisonV3(BaseModel):
    model_config = ConfigDict(extra="forbid")

    split: DatasetSplit
    baseline_engine_version: str
    candidate_engine_version: str
    metrics: list[PairedMetricDelta]
    case_type_deltas: dict[str, dict[str, float]] = Field(default_factory=dict)


def calculate_knowledge_retrieval_metrics_v3(
    dataset: KnowledgeRetrievalDatasetV3,
    observations: list[KnowledgeRetrievalObservationV3],
    *,
    split: DatasetSplit,
    vector_validity_rate: float = 1.0,
) -> KnowledgeRetrievalMetricsV3:
    cases = [case for case in dataset.cases if case.split == split]
    case_ids = {case.case_id for case in cases}
    matches = [item for item in observations if item.case_id in case_ids]
    by_case: dict[str, list[KnowledgeRetrievalObservationV3]] = {}
    for observation in matches:
        by_case.setdefault(observation.case_id, []).append(observation)
    complete = {case_id: values[0] for case_id, values in by_case.items() if len(values) == 1}

    engine_versions = {item.engine_version for item in complete.values()}
    if len(engine_versions) > 1:
        raise ValueError("one metrics calculation cannot mix engine versions")
    engine_version = next(iter(engine_versions), "unknown")

    evidence_dataset = dataset.as_v2(split)
    evidence_observations = [
        complete[case.case_id].as_v2()
        for case in dataset.evidence_cases(split)
        if case.case_id in complete
    ]
    base = calculate_knowledge_retrieval_metrics_v2(
        evidence_dataset,
        evidence_observations,
        vector_validity_rate=vector_validity_rate,
    )

    evidence_cases = dataset.evidence_cases(split)
    hit_at_1 = _ratio(
        sum(
            bool(observation.retrieved)
            and observation.retrieved[0].chunk_id in case.primary_relevant_chunk_ids
            for case in evidence_cases
            if (observation := complete.get(case.case_id)) is not None
        ),
        len(evidence_cases),
    )
    hard_negative_cases = [
        case for case in cases if case.case_type == "hard_negative"
    ]
    hard_negative_fp = _ratio(
        sum(
            bool(
                set(item.chunk_id for item in observation.retrieved)
                & set(case.excluded_chunk_ids)
            )
            for case in hard_negative_cases
            if (observation := complete.get(case.case_id)) is not None
        ),
        len(hard_negative_cases),
    )

    true_no_evidence = {case.case_id for case in dataset.no_evidence_cases(split)}
    declared_no_evidence = {
        case_id
        for case_id, observation in complete.items()
        if observation.declared_no_evidence
    }
    no_evidence_precision = _ratio(
        len(true_no_evidence & declared_no_evidence), len(declared_no_evidence)
    )
    no_evidence_recall = _ratio(
        len(true_no_evidence & declared_no_evidence), len(true_no_evidence)
    )
    no_evidence_f1 = (
        2
        * no_evidence_precision
        * no_evidence_recall
        / (no_evidence_precision + no_evidence_recall)
        if no_evidence_precision + no_evidence_recall
        else 0.0
    )

    semantic_only, lexical_only, hybrid = _channel_wins(
        dataset, complete, split=split
    )
    evidence_precision = _evidence_precision(evidence_cases, complete)
    domain_routing = _routing_accuracy(
        evidence_cases,
        complete,
        predicate=lambda case, item: item.domain in case.allowed_domains,
    )
    topic_routing = _routing_accuracy(
        evidence_cases,
        complete,
        predicate=lambda case, item: bool(
            set(item.tags).intersection(case.canonical_tags)
        ),
    )
    case_type_breakdown = _case_type_breakdown(cases, complete)
    return KnowledgeRetrievalMetricsV3(
        split=split,
        engine_version=engine_version,
        case_count=len(cases),
        observation_completeness_rate=_ratio(len(complete), len(cases)),
        filter_correctness_rate=base.filter_correctness_rate,
        vector_validity_rate=base.vector_validity_rate,
        recall_at_5=base.recall_at_5,
        mrr_at_5=base.mrr_at_5,
        ndcg_at_5=base.ndcg_at_5,
        hit_at_1=hit_at_1,
        hard_negative_false_positive_rate=hard_negative_fp,
        no_evidence_precision=no_evidence_precision,
        no_evidence_recall=no_evidence_recall,
        no_evidence_f1=no_evidence_f1,
        evidence_precision_at_5=evidence_precision,
        domain_routing_accuracy=domain_routing,
        topic_routing_accuracy=topic_routing,
        cross_channel_contribution_rate=(
            semantic_only + lexical_only + hybrid
        ),
        evidence_replay_stability_rate=base.evidence_replay_stability_rate,
        excluded_chunk_violation_rate=base.excluded_chunk_violation_rate,
        p95_latency_ms=base.p95_latency_ms,
        semantic_only_win_rate=semantic_only,
        lexical_only_win_rate=lexical_only,
        hybrid_win_rate=hybrid,
        case_type_counts=dict(Counter(case.case_type for case in cases)),
        case_type_breakdown=case_type_breakdown,
    )


def compare_knowledge_retrieval_metrics_v3(
    baseline: KnowledgeRetrievalMetricsV3,
    candidate: KnowledgeRetrievalMetricsV3,
) -> KnowledgeRetrievalComparisonV3:
    if baseline.split != candidate.split:
        raise ValueError("paired comparison requires the same dataset split")
    metric_names = (
        "recall_at_5",
        "mrr_at_5",
        "ndcg_at_5",
        "hit_at_1",
        "filter_correctness_rate",
        "vector_validity_rate",
        "hard_negative_false_positive_rate",
        "no_evidence_precision",
        "no_evidence_recall",
        "no_evidence_f1",
        "evidence_precision_at_5",
        "domain_routing_accuracy",
        "topic_routing_accuracy",
        "cross_channel_contribution_rate",
        "evidence_replay_stability_rate",
        "observation_completeness_rate",
        "excluded_chunk_violation_rate",
        "p95_latency_ms",
    )
    return KnowledgeRetrievalComparisonV3(
        split=baseline.split,
        baseline_engine_version=baseline.engine_version,
        candidate_engine_version=candidate.engine_version,
        metrics=[
            PairedMetricDelta(
                metric=name,
                baseline=float(getattr(baseline, name)),
                candidate=float(getattr(candidate, name)),
                delta=float(getattr(candidate, name) - getattr(baseline, name)),
            )
            for name in metric_names
        ],
        case_type_deltas=_case_type_deltas(
            baseline.case_type_breakdown,
            candidate.case_type_breakdown,
        ),
    )


def _channel_wins(
    dataset: KnowledgeRetrievalDatasetV3,
    observations: dict[str, KnowledgeRetrievalObservationV3],
    *,
    split: DatasetSplit,
) -> tuple[float, float, float]:
    counts: Counter[str] = Counter()
    evaluated = 0
    for case in dataset.evidence_cases(split):
        observation = observations.get(case.case_id)
        if observation is None:
            continue
        relevant = set(case.primary_relevant_chunk_ids)
        semantic = bool(relevant & set(observation.semantic_hit_ids))
        lexical = bool(relevant & set(observation.lexical_hit_ids))
        if not semantic and not lexical:
            continue
        evaluated += 1
        if semantic and lexical:
            counts["hybrid"] += 1
        elif semantic:
            counts["semantic"] += 1
        else:
            counts["lexical"] += 1
    return (
        _ratio(counts["semantic"], evaluated),
        _ratio(counts["lexical"], evaluated),
        _ratio(counts["hybrid"], evaluated),
    )


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _evidence_precision(cases, observations) -> float:
    relevant_returned = 0
    returned = 0
    for case in cases:
        observation = observations.get(case.case_id)
        if observation is None:
            continue
        relevant = set(case.primary_relevant_chunk_ids) | set(
            case.accepted_related_chunk_ids
        )
        retrieved = observation.retrieved[:5]
        returned += len(retrieved)
        relevant_returned += sum(item.chunk_id in relevant for item in retrieved)
    return _ratio(relevant_returned, returned) if returned else 1.0


def _routing_accuracy(cases, observations, *, predicate) -> float:
    correct = 0
    returned = 0
    for case in cases:
        observation = observations.get(case.case_id)
        if observation is None:
            continue
        for item in observation.retrieved[:5]:
            returned += 1
            correct += int(predicate(case, item))
    return _ratio(correct, returned) if returned else 1.0


def _case_type_breakdown(cases, observations) -> dict[str, dict[str, float | int]]:
    result: dict[str, dict[str, float | int]] = {}
    grouped = {case_type: [] for case_type in sorted({case.case_type for case in cases})}
    for case in cases:
        grouped[case.case_type].append(case)
    for case_type, type_cases in grouped.items():
        observed = [case for case in type_cases if case.case_id in observations]
        evidence_cases = [case for case in type_cases if not case.expected_no_evidence]
        recall_total = 0.0
        reciprocal_rank_total = 0.0
        ndcg_total = 0.0
        hit_at_1 = 0
        relevant_returned = 0
        returned = 0
        excluded_violations = 0
        no_evidence_true_positive = 0
        no_evidence_declared = 0
        no_evidence_count = sum(case.expected_no_evidence for case in type_cases)
        correct_domain = 0
        correct_topic = 0
        for case in observed:
            observation = observations[case.case_id]
            retrieved = observation.retrieved[:5]
            retrieved_ids = [item.chunk_id for item in retrieved]
            no_evidence_declared += int(observation.declared_no_evidence)
            if case.expected_no_evidence:
                no_evidence_true_positive += int(observation.declared_no_evidence)
                continue
            primary = set(case.primary_relevant_chunk_ids)
            relevant = primary | set(case.accepted_related_chunk_ids)
            recall_total += len(primary.intersection(retrieved_ids)) / len(primary)
            primary_rank = next(
                (
                    rank
                    for rank, chunk_id in enumerate(retrieved_ids, 1)
                    if chunk_id in primary
                ),
                None,
            )
            reciprocal_rank_total += 1 / primary_rank if primary_rank else 0.0
            accepted = set(case.accepted_related_chunk_ids)

            def relevance(chunk_id: str) -> int:
                if chunk_id in primary:
                    return 3
                if chunk_id in accepted:
                    return 1
                return 0

            dcg = sum(
                relevance(chunk_id) / math.log2(rank + 1)
                for rank, chunk_id in enumerate(retrieved_ids, 1)
            )
            ideal = sorted(
                [3] * len(primary) + [1] * len(accepted),
                reverse=True,
            )[:5]
            idcg = sum(
                value / math.log2(rank + 1)
                for rank, value in enumerate(ideal, 1)
            )
            ndcg_total += dcg / idcg if idcg else 0.0
            hit_at_1 += int(bool(retrieved_ids) and retrieved_ids[0] in primary)
            excluded_violations += int(
                bool(set(retrieved_ids).intersection(case.excluded_chunk_ids))
            )
            returned += len(retrieved)
            relevant_returned += sum(item.chunk_id in relevant for item in retrieved)
            correct_domain += sum(item.domain in case.allowed_domains for item in retrieved)
            correct_topic += sum(
                bool(set(item.tags).intersection(case.canonical_tags))
                for item in retrieved
            )
        evidence_count = len(evidence_cases)
        result[case_type] = {
            "case_count": len(type_cases),
            "observation_completeness_rate": _ratio(len(observed), len(type_cases)),
            "hit_at_1": _ratio(hit_at_1, evidence_count),
            "recall_at_5": _ratio(recall_total, evidence_count),
            "mrr_at_5": _ratio(reciprocal_rank_total, evidence_count),
            "ndcg_at_5": _ratio(ndcg_total, evidence_count),
            "evidence_precision_at_5": (
                _ratio(relevant_returned, returned) if returned else 1.0
            ),
            "domain_routing_accuracy": (
                _ratio(correct_domain, returned) if returned else 1.0
            ),
            "topic_routing_accuracy": (
                _ratio(correct_topic, returned) if returned else 1.0
            ),
            "excluded_chunk_violation_rate": _ratio(
                excluded_violations, evidence_count
            ),
            "hard_negative_false_positive_rate": (
                _ratio(excluded_violations, evidence_count)
                if case_type == "hard_negative"
                else 0.0
            ),
            "no_evidence_recall": _ratio(
                no_evidence_true_positive, no_evidence_count
            ),
            "no_evidence_precision": _ratio(
                no_evidence_true_positive, no_evidence_declared
            ),
        }
    return result


def _case_type_deltas(baseline, candidate) -> dict[str, dict[str, float]]:
    if set(baseline) != set(candidate):
        raise ValueError("paired metrics require identical case-type breakdowns")
    deltas: dict[str, dict[str, float]] = {}
    for case_type in sorted(baseline):
        if set(baseline[case_type]) != set(candidate[case_type]):
            raise ValueError(
                f"paired metrics have different fields for case type {case_type}"
            )
        deltas[case_type] = {
            metric: float(candidate[case_type][metric])
            - float(baseline[case_type][metric])
            for metric in sorted(baseline[case_type])
            if metric != "case_count"
        }
    return deltas
