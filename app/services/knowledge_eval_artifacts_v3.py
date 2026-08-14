from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.knowledge.evidence import EvidenceDecision

from app.domain.knowledge.retrieval import (
    ResolvedRetrievalProfile,
    RetrievalAvailability,
    RetrievalHardConstraints,
    RetrievalIntent,
    RetrievalRequest,
    RetrievalResult,
    RetrievalRoutingHints,
)
from app.services.knowledge_eval_dataset_v3 import (
    DatasetSplit,
    KnowledgeRetrievalDatasetV3,
)
from app.services.knowledge_eval_metrics_v2 import RetrievedKnowledgeItemV2
from app.services.knowledge_eval_metrics_v3 import (
    KnowledgeRetrievalComparisonV3,
    KnowledgeRetrievalMetricsV3,
    KnowledgeRetrievalObservationV3,
    calculate_knowledge_retrieval_metrics_v3,
    compare_knowledge_retrieval_metrics_v3,
)


def canonical_sha256(value) -> str:
    value = _canonical_value(value)
    payload = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _canonical_value(value):
    if isinstance(value, BaseModel):
        return _canonical_value(value.model_dump(mode="json"))
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("canonical timestamps must be timezone-aware")
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, dict):
        return {str(key): _canonical_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical_value(item) for item in value]
    return value


class KnowledgeEvalEngineIdentityV3(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    engine_version: str = Field(min_length=1)
    code_revision: str = Field(min_length=1)
    code_tree_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    profile_id: str = Field(min_length=1)
    profile_version: str = Field(min_length=1)
    profile_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    provider_name: str = Field(min_length=1)
    model_name: str = Field(min_length=1)
    model_revision: str = Field(min_length=1)
    embedding_dimension: int = Field(ge=1)
    corpus_version: str = Field(min_length=1)
    corpus_manifest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class KnowledgeEvalCandidateV3(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    chunk_id: str = Field(min_length=1)
    rank: int = Field(ge=1)
    score: float
    channels: tuple[str, ...] = ()


class KnowledgeEvalCaseResultV3(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str = Field(min_length=1)
    availability: RetrievalAvailability
    candidates: tuple[KnowledgeEvalCandidateV3, ...] = ()
    selected_evidence_ids: tuple[str, ...] = ()
    bound_evidence_ids: tuple[str, ...] = ()
    replayed_evidence_ids: tuple[str, ...] = ()
    semantic_hit_ids: tuple[str, ...] = ()
    lexical_hit_ids: tuple[str, ...] = ()
    declared_no_evidence: bool = False
    latency_ms: float = Field(ge=0)
    reason_codes: tuple[str, ...] = ()


class RetrievalDiagnosticCandidateV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    chunk_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    domain: str = Field(min_length=1)
    topic: str = ""
    source_type: str = Field(min_length=1)
    tags: tuple[str, ...] = ()
    content_sha256: str = ""
    semantic_rank: int | None = Field(default=None, ge=1)
    semantic_score: float | None = None
    lexical_rank: int | None = Field(default=None, ge=1)
    lexical_score: float | None = None
    fusion_rank: int | None = Field(default=None, ge=1)
    fusion_score: float | None = None
    rerank_rank: int | None = Field(default=None, ge=1)
    rerank_score: float | None = None
    channel_hits: tuple[str, ...] = ()
    matched_terms: tuple[str, ...] = ()
    ranking_explanation: dict | None = None
    selected: bool = False


class RetrievalDiagnosticSnapshotV1(BaseModel):
    """Privacy-safe immutable sidecar for exact five-stage replay."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = "retrieval-diagnostic-snapshot-v1"
    created_at: datetime
    artifact_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    case_id: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    trace_schema_version: Literal["retrieval-trace-v2", "retrieval-trace-v3"]
    query_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    query_character_count: int = Field(ge=1, le=4000)
    engine_version: str = Field(min_length=1)
    profile_id: str = Field(min_length=1)
    profile_version: str = Field(min_length=1)
    component_versions: dict[str, str]
    candidates: tuple[RetrievalDiagnosticCandidateV1, ...]
    selected_evidence_ids: tuple[str, ...] = ()
    evidence_decision: EvidenceDecision | None = None
    latency_breakdown_ms: dict[str, float | None]
    degraded_reasons: tuple[str, ...] = ()
    snapshot_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def validate_integrity(self):
        if self.created_at.tzinfo is None:
            raise ValueError("snapshot created_at must be timezone-aware")
        if len({item.chunk_id for item in self.candidates}) != len(self.candidates):
            raise ValueError("snapshot candidates must be unique")
        payload = self.model_dump(mode="json", exclude={"snapshot_sha256"})
        if canonical_sha256(payload) != self.snapshot_sha256:
            raise ValueError("snapshot SHA-256 mismatch")
        return self


class KnowledgeEvalArtifactV3(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = "knowledge-eval-artifact-v3"
    created_at: datetime
    dataset_version: str = Field(min_length=1)
    dataset_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    split: DatasetSplit
    identity: KnowledgeEvalEngineIdentityV3
    vector_validity_rate: float = Field(ge=0, le=1)
    metrics: KnowledgeRetrievalMetricsV3
    cases: tuple[KnowledgeEvalCaseResultV3, ...]
    artifact_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def validate_integrity(self):
        if self.created_at.tzinfo is None:
            raise ValueError("artifact created_at must be timezone-aware")
        if self.metrics.split != self.split:
            raise ValueError("artifact metrics split mismatch")
        if self.metrics.engine_version != self.identity.engine_version:
            raise ValueError("artifact metrics engine mismatch")
        if len(self.cases) != self.metrics.case_count:
            raise ValueError("artifact case count does not match metrics")
        if len({case.case_id for case in self.cases}) != len(self.cases):
            raise ValueError("artifact case IDs must be unique")
        payload = self.model_dump(mode="json", exclude={"artifact_sha256"})
        if canonical_sha256(payload) != self.artifact_sha256:
            raise ValueError("artifact SHA-256 mismatch")
        return self


class KnowledgeEvalPairedArtifactV3(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = "knowledge-eval-paired-v3"
    created_at: datetime
    dataset_version: str
    dataset_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    corpus_manifest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    split: DatasetSplit
    baseline_artifact_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    candidate_artifact_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    threshold_registration_sha256: str | None = Field(
        default=None,
        pattern=r"^[a-f0-9]{64}$",
    )
    comparison: KnowledgeRetrievalComparisonV3
    thresholds_passed: bool | None = None
    failed_thresholds: tuple[str, ...] = ()
    case_ids: tuple[str, ...]
    artifact_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def validate_integrity(self):
        if self.created_at.tzinfo is None:
            raise ValueError("paired artifact created_at must be timezone-aware")
        if self.comparison.split != self.split:
            raise ValueError("paired artifact comparison split mismatch")
        if self.split == "holdout" and self.threshold_registration_sha256 is None:
            raise ValueError("holdout paired artifact requires threshold registration")
        if self.split == "holdout" and self.thresholds_passed is None:
            raise ValueError("holdout paired artifact requires a threshold decision")
        if self.thresholds_passed is True and self.failed_thresholds:
            raise ValueError("passing paired artifact cannot contain failed thresholds")
        if self.thresholds_passed is False and not self.failed_thresholds:
            raise ValueError("failing paired artifact must contain failed thresholds")
        payload = self.model_dump(mode="json", exclude={"artifact_sha256"})
        if canonical_sha256(payload) != self.artifact_sha256:
            raise ValueError("paired artifact SHA-256 mismatch")
        return self


class KnowledgeEvalThresholdRegistrationV3(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = "knowledge-eval-threshold-registration-v3"
    registered_at: datetime
    dataset_version: str = Field(min_length=1)
    dataset_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    corpus_manifest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    split: DatasetSplit
    baseline_artifact_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    candidate_engine_version: str = Field(min_length=1)
    candidate_code_revision: str = Field(min_length=1)
    candidate_code_tree_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    candidate_profile_id: str = Field(min_length=1)
    candidate_profile_version: str = Field(min_length=1)
    candidate_profile_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    candidate_provider_name: str = Field(min_length=1)
    candidate_model_name: str = Field(min_length=1)
    candidate_model_revision: str = Field(min_length=1)
    candidate_embedding_dimension: int = Field(ge=1)
    primary_metric: str = Field(min_length=1)
    minimum_deltas: dict[str, float]
    maximum_deltas: dict[str, float]
    absolute_minimums: dict[str, float]
    absolute_maximums: dict[str, float]
    profile_p95_budgets_ms: dict[str, float]
    profile_p95_relative_limits: dict[str, float]
    rationale_record_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    registration_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def validate_integrity(self):
        if self.registered_at.tzinfo is None:
            raise ValueError("threshold registration time must be timezone-aware")
        if self.split != "holdout":
            raise ValueError("threshold registration is required for holdout")
        registered_metrics = (
            set(self.minimum_deltas)
            | set(self.maximum_deltas)
            | set(self.absolute_minimums)
            | set(self.absolute_maximums)
        )
        known_metrics = {
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
        }
        unknown = sorted(registered_metrics - known_metrics)
        if unknown:
            raise ValueError(
                "threshold registration has unknown metrics: " + ", ".join(unknown)
            )
        all_thresholds = (
            list(self.minimum_deltas.values())
            + list(self.maximum_deltas.values())
            + list(self.absolute_minimums.values())
            + list(self.absolute_maximums.values())
            + list(self.profile_p95_budgets_ms.values())
            + list(self.profile_p95_relative_limits.values())
        )
        if any(not math.isfinite(value) for value in all_thresholds):
            raise ValueError("threshold values must be finite")
        required_metrics = {
            "recall_at_5",
            "mrr_at_5",
            "ndcg_at_5",
            "hit_at_1",
            "hard_negative_false_positive_rate",
            "no_evidence_f1",
            "excluded_chunk_violation_rate",
            "evidence_replay_stability_rate",
            "observation_completeness_rate",
            "p95_latency_ms",
        }
        missing = sorted(required_metrics - registered_metrics)
        if missing:
            raise ValueError(
                "threshold registration is missing release metrics: "
                + ", ".join(missing)
            )
        if self.primary_metric not in registered_metrics:
            raise ValueError("primary metric must have a registered threshold")
        if not self.profile_p95_budgets_ms:
            raise ValueError("threshold registration requires profile latency budgets")
        if set(self.profile_p95_budgets_ms) != set(self.profile_p95_relative_limits):
            raise ValueError(
                "absolute and relative profile latency budgets must cover the same profiles"
            )
        if any(value <= 0 for value in self.profile_p95_budgets_ms.values()):
            raise ValueError("profile latency budgets must be positive")
        if any(value <= 0 for value in self.profile_p95_relative_limits.values()):
            raise ValueError("relative profile latency limits must be positive")
        if self.candidate_profile_id not in self.profile_p95_budgets_ms:
            raise ValueError(
                "threshold registration requires latency budgets for candidate profile"
            )
        payload = self.model_dump(mode="json", exclude={"registration_sha256"})
        if canonical_sha256(payload) != self.registration_sha256:
            raise ValueError("threshold registration SHA-256 mismatch")
        return self


def build_threshold_registration_v3(
    baseline: KnowledgeEvalArtifactV3,
    *,
    primary_metric: str,
    minimum_deltas: dict[str, float],
    maximum_deltas: dict[str, float],
    absolute_minimums: dict[str, float],
    absolute_maximums: dict[str, float],
    profile_p95_budgets_ms: dict[str, float],
    profile_p95_relative_limits: dict[str, float],
    candidate_engine_version: str,
    candidate_code_revision: str,
    candidate_code_tree_sha256: str,
    candidate_profile: ResolvedRetrievalProfile,
    rationale_record_sha256: str,
    registered_at: datetime | None = None,
) -> KnowledgeEvalThresholdRegistrationV3:
    payload = {
        "schema_version": "knowledge-eval-threshold-registration-v3",
        "registered_at": registered_at or datetime.now(timezone.utc),
        "dataset_version": baseline.dataset_version,
        "dataset_sha256": baseline.dataset_sha256,
        "corpus_manifest_sha256": baseline.identity.corpus_manifest_sha256,
        "split": baseline.split,
        "baseline_artifact_sha256": baseline.artifact_sha256,
        "candidate_engine_version": candidate_engine_version,
        "candidate_code_revision": candidate_code_revision,
        "candidate_code_tree_sha256": candidate_code_tree_sha256,
        "candidate_profile_id": candidate_profile.profile_id,
        "candidate_profile_version": candidate_profile.profile_version,
        "candidate_profile_sha256": canonical_sha256(
            candidate_profile.model_dump(mode="json")
        ),
        "candidate_provider_name": baseline.identity.provider_name,
        "candidate_model_name": baseline.identity.model_name,
        "candidate_model_revision": baseline.identity.model_revision,
        "candidate_embedding_dimension": baseline.identity.embedding_dimension,
        "primary_metric": primary_metric,
        "minimum_deltas": minimum_deltas,
        "maximum_deltas": maximum_deltas,
        "absolute_minimums": absolute_minimums,
        "absolute_maximums": absolute_maximums,
        "profile_p95_budgets_ms": profile_p95_budgets_ms,
        "profile_p95_relative_limits": profile_p95_relative_limits,
        "rationale_record_sha256": rationale_record_sha256,
    }
    return KnowledgeEvalThresholdRegistrationV3(
        **payload,
        registration_sha256=canonical_sha256(payload),
    )


def build_engine_identity_v3(
    *,
    engine_version: str,
    code_revision: str,
    code_tree_sha256: str,
    profile: ResolvedRetrievalProfile,
    repository,
    corpus_version: str,
    corpus_manifest_sha256: str,
) -> KnowledgeEvalEngineIdentityV3:
    provider = getattr(repository, "embedding_provider", None)
    return KnowledgeEvalEngineIdentityV3(
        engine_version=engine_version,
        code_revision=code_revision,
        code_tree_sha256=code_tree_sha256,
        profile_id=profile.profile_id,
        profile_version=profile.profile_version,
        profile_sha256=canonical_sha256(profile.model_dump(mode="json")),
        provider_name=str(getattr(provider, "provider_name", "unknown")),
        model_name=str(getattr(provider, "model_name", "unknown")),
        model_revision=str(getattr(provider, "model_revision", "unknown")),
        embedding_dimension=int(
            getattr(provider, "dimension", getattr(repository, "embedding_dimension", 0))
        ),
        corpus_version=corpus_version,
        corpus_manifest_sha256=corpus_manifest_sha256,
    )


def evaluate_knowledge_engine_v3(
    dataset: KnowledgeRetrievalDatasetV3,
    engine,
    repository,
    *,
    split: DatasetSplit,
    profile: ResolvedRetrievalProfile,
    identity: KnowledgeEvalEngineIdentityV3,
    vector_validity_rate: float = 1.0,
    created_at: datetime | None = None,
    result_observer: Callable[[str, RetrievalResult], None] | None = None,
) -> KnowledgeEvalArtifactV3:
    if identity.corpus_manifest_sha256 != dataset.corpus_manifest_sha256:
        raise ValueError("engine identity corpus manifest does not match dataset")
    if identity.profile_sha256 != canonical_sha256(profile.model_dump(mode="json")):
        raise ValueError("engine identity profile does not match resolved profile")
    observations: list[KnowledgeRetrievalObservationV3] = []
    case_results: list[KnowledgeEvalCaseResultV3] = []
    for case in (item for item in dataset.cases if item.split == split):
        hard_filters = (
            {
                "tags": tuple(case.canonical_tags),
                "domains": tuple(case.allowed_domains),
            }
            if profile.routing_policy == "hard"
            else {}
        )
        request = RetrievalRequest(
            request_id=f"eval-v3-{case.case_id}",
            query_text=case.query_text,
            intent=RetrievalIntent.EVAL,
            hard_constraints=RetrievalHardConstraints(
                source_types=tuple(case.source_types),
                filters=hard_filters,
            ),
            routing_hints=RetrievalRoutingHints(
                domains=tuple(case.allowed_domains),
                canonical_tags=tuple(case.canonical_tags),
            ),
            profile_id=profile.profile_id,
        )
        try:
            result = engine.retrieve(request, profile)
        except Exception:
            result = _unavailable_result(request, profile, identity.engine_version)
        if result_observer is not None:
            result_observer(case.case_id, result)
        observation, case_result = _observe_case(
            case.case_id,
            result,
            repository,
            engine_version=identity.engine_version,
        )
        observations.append(observation)
        case_results.append(case_result)
    metrics = calculate_knowledge_retrieval_metrics_v3(
        dataset,
        observations,
        split=split,
        vector_validity_rate=vector_validity_rate,
    )
    payload = {
        "schema_version": "knowledge-eval-artifact-v3",
        "created_at": created_at or datetime.now(timezone.utc),
        "dataset_version": dataset.version,
        "dataset_sha256": canonical_sha256(dataset.model_dump(mode="json")),
        "split": split,
        "identity": identity,
        "vector_validity_rate": vector_validity_rate,
        "metrics": metrics,
        "cases": tuple(case_results),
    }
    return KnowledgeEvalArtifactV3(
        **payload,
        artifact_sha256=canonical_sha256(payload),
    )


def compare_knowledge_eval_artifacts_v3(
    baseline: KnowledgeEvalArtifactV3,
    candidate: KnowledgeEvalArtifactV3,
    *,
    threshold_registration: KnowledgeEvalThresholdRegistrationV3 | None = None,
    created_at: datetime | None = None,
) -> KnowledgeEvalPairedArtifactV3:
    for field_name in ("dataset_version", "dataset_sha256", "split"):
        if getattr(baseline, field_name) != getattr(candidate, field_name):
            raise ValueError(f"paired artifacts have different {field_name}")
    if (
        baseline.identity.corpus_manifest_sha256
        != candidate.identity.corpus_manifest_sha256
    ):
        raise ValueError("paired artifacts have different corpus manifests")
    for field_name in (
        "provider_name",
        "model_name",
        "model_revision",
        "embedding_dimension",
    ):
        if getattr(baseline.identity, field_name) != getattr(
            candidate.identity, field_name
        ):
            raise ValueError(f"paired artifacts have different {field_name}")
    baseline_ids = tuple(case.case_id for case in baseline.cases)
    candidate_ids = tuple(case.case_id for case in candidate.cases)
    if baseline_ids != candidate_ids:
        raise ValueError("paired artifacts require the same ordered case IDs")
    if baseline.identity.engine_version == candidate.identity.engine_version:
        raise ValueError("paired artifacts require different engine versions")
    if baseline.split == "holdout":
        if threshold_registration is None:
            raise ValueError(
                "holdout comparison requires a pre-registered threshold artifact"
            )
        _validate_threshold_registration(threshold_registration, baseline, candidate)
    comparison = compare_knowledge_retrieval_metrics_v3(
        baseline.metrics, candidate.metrics
    )
    failed_thresholds = (
        _failed_thresholds(comparison, threshold_registration, candidate)
        if threshold_registration is not None
        else ()
    )
    payload = {
        "schema_version": "knowledge-eval-paired-v3",
        "created_at": created_at or datetime.now(timezone.utc),
        "dataset_version": baseline.dataset_version,
        "dataset_sha256": baseline.dataset_sha256,
        "corpus_manifest_sha256": baseline.identity.corpus_manifest_sha256,
        "split": baseline.split,
        "baseline_artifact_sha256": baseline.artifact_sha256,
        "candidate_artifact_sha256": candidate.artifact_sha256,
        "threshold_registration_sha256": (
            threshold_registration.registration_sha256
            if threshold_registration is not None
            else None
        ),
        "comparison": comparison,
        "thresholds_passed": (
            not failed_thresholds if threshold_registration is not None else None
        ),
        "failed_thresholds": failed_thresholds,
        "case_ids": baseline_ids,
    }
    return KnowledgeEvalPairedArtifactV3(
        **payload,
        artifact_sha256=canonical_sha256(payload),
    )


def write_frozen_eval_artifact(artifact: BaseModel, path: Path | str) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with target.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(artifact.model_dump_json(indent=2))
            stream.write("\n")
    except FileExistsError as exc:
        raise FileExistsError(f"refusing to overwrite frozen eval artifact: {target}") from exc
    return target


def load_eval_artifact_v3(path: Path | str) -> KnowledgeEvalArtifactV3:
    return KnowledgeEvalArtifactV3.model_validate_json(
        Path(path).read_text(encoding="utf-8")
    )


def build_retrieval_diagnostic_snapshot_v1(
    *,
    artifact_sha256: str,
    case_id: str,
    result: RetrievalResult,
    created_at: datetime | None = None,
) -> RetrievalDiagnosticSnapshotV1:
    selected = {item.chunk_id for item in result.selected_evidence}
    query_facts = result.trace.sanitized_query_facts
    if query_facts is None:
        raise ValueError("retrieval result has no sanitized query facts")
    versions = (
        result.trace.component_versions.model_dump(mode="json")
        if result.trace.component_versions is not None
        else {}
    )
    candidates = tuple(
        RetrievalDiagnosticCandidateV1(
            chunk_id=item.chunk_id,
            title=item.chunk.title,
            domain=item.chunk.domain,
            topic=str(item.chunk.metadata.get("topic") or ""),
            source_type=item.chunk.source_type,
            tags=tuple(item.chunk.tags),
            content_sha256=str(item.chunk.metadata.get("content_sha256") or ""),
            semantic_rank=item.semantic_rank,
            semantic_score=item.semantic_score,
            lexical_rank=item.lexical_rank,
            lexical_score=item.lexical_score,
            fusion_rank=item.fusion_rank,
            fusion_score=item.fusion_score,
            rerank_rank=item.rerank_rank,
            rerank_score=item.rerank_score,
            channel_hits=tuple(item.channel_hits),
            matched_terms=tuple(item.matched_terms),
            ranking_explanation=(
                item.ranking_explanation.model_dump(mode="json")
                if item.ranking_explanation is not None
                else None
            ),
            selected=item.chunk_id in selected,
        )
        for item in result.candidates
    )
    payload = {
        "schema_version": "retrieval-diagnostic-snapshot-v1",
        "created_at": created_at or datetime.now(timezone.utc),
        "artifact_sha256": artifact_sha256,
        "case_id": case_id,
        "request_id": result.request_id,
        "trace_schema_version": result.trace.trace_schema_version,
        "query_sha256": query_facts.query_sha256,
        "query_character_count": query_facts.character_count,
        "engine_version": result.retrieval_engine_version,
        "profile_id": result.trace.profile_id,
        "profile_version": result.profile_version,
        "component_versions": versions,
        "candidates": candidates,
        "selected_evidence_ids": tuple(item.chunk_id for item in result.selected_evidence),
        "evidence_decision": result.evidence_decision,
        "latency_breakdown_ms": dict(result.trace.latency_breakdown_ms),
        "degraded_reasons": tuple(result.degraded_reasons),
    }
    return RetrievalDiagnosticSnapshotV1(
        **payload,
        snapshot_sha256=canonical_sha256(payload),
    )


def load_retrieval_diagnostic_snapshot_v1(
    path: Path | str,
) -> RetrievalDiagnosticSnapshotV1:
    return RetrievalDiagnosticSnapshotV1.model_validate_json(
        Path(path).read_text(encoding="utf-8")
    )


def write_retrieval_diagnostic_snapshots_v1(
    artifact: KnowledgeEvalArtifactV3,
    results: dict[str, RetrievalResult],
    root: Path | str,
) -> Path:
    """Atomically publish validated sidecars from the artifact's retrieval run."""

    expected_ids = tuple(item.case_id for item in artifact.cases)
    if tuple(results) != expected_ids:
        raise ValueError("snapshot results must match artifact case order")
    target = Path(root) / artifact.artifact_sha256
    if target.exists():
        raise FileExistsError(f"refusing to overwrite frozen snapshots: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.parent / f".{target.name}.staging"
    if staging.exists():
        raise FileExistsError(f"snapshot staging path already exists: {staging}")
    staging.mkdir()
    try:
        for case_id, result in results.items():
            snapshot = build_retrieval_diagnostic_snapshot_v1(
                artifact_sha256=artifact.artifact_sha256,
                case_id=case_id,
                result=result,
                created_at=artifact.created_at,
            )
            path = staging / f"{case_id}.json"
            path.write_text(
                snapshot.model_dump_json(indent=2) + "\n",
                encoding="utf-8",
            )
            load_retrieval_diagnostic_snapshot_v1(path)
        staging.rename(target)
    except Exception:
        import shutil

        shutil.rmtree(staging, ignore_errors=True)
        raise
    return target


def load_threshold_registration_v3(
    path: Path | str,
) -> KnowledgeEvalThresholdRegistrationV3:
    return KnowledgeEvalThresholdRegistrationV3.model_validate_json(
        Path(path).read_text(encoding="utf-8")
    )


def _validate_threshold_registration(registration, baseline, candidate) -> None:
    expected = {
        "dataset_version": baseline.dataset_version,
        "dataset_sha256": baseline.dataset_sha256,
        "corpus_manifest_sha256": baseline.identity.corpus_manifest_sha256,
        "split": baseline.split,
        "baseline_artifact_sha256": baseline.artifact_sha256,
    }
    for field_name, value in expected.items():
        if getattr(registration, field_name) != value:
            raise ValueError(
                f"threshold registration has different {field_name}"
            )
    if registration.registered_at <= baseline.created_at:
        raise ValueError(
            "thresholds cannot be registered before the baseline artifact exists"
        )
    if registration.registered_at >= candidate.created_at:
        raise ValueError(
            "thresholds must be registered before the candidate holdout run"
        )
    validate_registered_candidate_v3(registration, candidate.identity)


def validate_registered_candidate_v3(
    registration: KnowledgeEvalThresholdRegistrationV3,
    identity: KnowledgeEvalEngineIdentityV3,
) -> None:
    expected = {
        "candidate_engine_version": identity.engine_version,
        "candidate_code_revision": identity.code_revision,
        "candidate_code_tree_sha256": identity.code_tree_sha256,
        "candidate_profile_id": identity.profile_id,
        "candidate_profile_version": identity.profile_version,
        "candidate_profile_sha256": identity.profile_sha256,
        "candidate_provider_name": identity.provider_name,
        "candidate_model_name": identity.model_name,
        "candidate_model_revision": identity.model_revision,
        "candidate_embedding_dimension": identity.embedding_dimension,
    }
    for field_name, value in expected.items():
        if getattr(registration, field_name) != value:
            raise ValueError(
                f"threshold registration has different {field_name}"
            )


def _failed_thresholds(comparison, registration, candidate) -> tuple[str, ...]:
    metrics = {item.metric: item for item in comparison.metrics}
    failed: list[str] = []
    for metric, threshold in registration.minimum_deltas.items():
        if metrics[metric].delta < threshold:
            failed.append(f"minimum_delta:{metric}")
    for metric, threshold in registration.maximum_deltas.items():
        if metrics[metric].delta > threshold:
            failed.append(f"maximum_delta:{metric}")
    for metric, threshold in registration.absolute_minimums.items():
        if metrics[metric].candidate < threshold:
            failed.append(f"absolute_minimum:{metric}")
    for metric, threshold in registration.absolute_maximums.items():
        if metrics[metric].candidate > threshold:
            failed.append(f"absolute_maximum:{metric}")
    profile_id = candidate.identity.profile_id
    if profile_id not in registration.profile_p95_budgets_ms:
        failed.append(f"missing_p95_budget:{profile_id}")
    elif (
        candidate.metrics.p95_latency_ms
        > registration.profile_p95_budgets_ms[profile_id]
    ):
        failed.append(f"profile_p95_budget:{profile_id}")
    if profile_id not in registration.profile_p95_relative_limits:
        failed.append(f"missing_relative_p95_budget:{profile_id}")
    else:
        baseline_p95 = metrics["p95_latency_ms"].baseline
        candidate_p95 = metrics["p95_latency_ms"].candidate
        relative_limit = registration.profile_p95_relative_limits[profile_id]
        if baseline_p95 <= 0:
            if candidate_p95 > 0:
                failed.append(f"relative_p95_unavailable:{profile_id}")
        elif candidate_p95 / baseline_p95 > relative_limit:
            failed.append(f"relative_p95_budget:{profile_id}")
    return tuple(sorted(failed))


def _observe_case(case_id, result, repository, *, engine_version):
    all_ranked = sorted(
        (
            item
            for item in result.candidates
            if item.rerank_rank is not None
        ),
        key=lambda item: (
            item.rerank_rank,
            item.chunk_id,
        ),
    )
    ranked = all_ranked[:5]
    retrieved = [
        RetrievedKnowledgeItemV2(
            chunk_id=item.chunk_id,
            domain=item.chunk.domain,
            source_type=item.chunk.source_type,
            tags=item.chunk.tags,
        )
        for item in ranked
    ]
    selected_ids = [item.chunk_id for item in result.selected_evidence]
    bound_ids = list(selected_ids)
    chunks_by_id = {
        item.chunk_id: item.chunk
        for item in all_ranked
    }
    chunks_by_id.update(
        {item.chunk_id: item for item in result.selected_evidence}
    )
    expected_hashes = {
        chunk_id: str(chunks_by_id[chunk_id].metadata.get("content_sha256") or "")
        for chunk_id in bound_ids
        if chunk_id in chunks_by_id
        and chunks_by_id[chunk_id].metadata.get("content_sha256")
    }
    replayed_ids: list[str] = []
    if bound_ids and len(expected_hashes) == len(bound_ids):
        try:
            lookup = repository.get_by_ids(bound_ids, expected_hashes=expected_hashes)
            if not lookup.missing and not lookup.version_mismatch:
                replayed_ids = [
                    str(
                        item.get("chunk_id", "")
                        if isinstance(item, dict)
                        else getattr(item, "chunk_id", "")
                    )
                    for item in lookup.found
                ]
                replayed_ids = [item for item in replayed_ids if item]
        except Exception:
            replayed_ids = []
    channel_hits = {
        channel.channel: list(channel.hit_ids[:5])
        for channel in result.trace.channels
    }
    semantic_ids = channel_hits.get("semantic", [])
    lexical_ids = channel_hits.get("lexical", [])
    declared_empty = (
        not ranked
        and result.availability == RetrievalAvailability.AVAILABLE
    )
    observation = KnowledgeRetrievalObservationV3(
        case_id=case_id,
        engine_version=engine_version,
        retrieved=retrieved,
        bound_evidence_ids=bound_ids,
        replayed_evidence_ids=replayed_ids,
        semantic_hit_ids=semantic_ids,
        lexical_hit_ids=lexical_ids,
        declared_no_evidence=declared_empty,
        latency_ms=result.latency_ms,
    )
    candidates = tuple(
        KnowledgeEvalCandidateV3(
            chunk_id=item.chunk_id,
            rank=rank,
            score=float(
                item.rerank_score
                if item.rerank_score is not None
                else item.fusion_score
                if item.fusion_score is not None
                else item.semantic_score
                if item.semantic_score is not None
                else item.lexical_score
                if item.lexical_score is not None
                else item.chunk.score or 0.0
            ),
            channels=tuple(sorted(set(item.channel_hits))),
        )
        for rank, item in enumerate(ranked, 1)
    )
    return observation, KnowledgeEvalCaseResultV3(
        case_id=case_id,
        availability=result.availability,
        candidates=candidates,
        selected_evidence_ids=tuple(selected_ids),
        bound_evidence_ids=tuple(bound_ids),
        replayed_evidence_ids=tuple(replayed_ids),
        semantic_hit_ids=tuple(semantic_ids),
        lexical_hit_ids=tuple(lexical_ids),
        declared_no_evidence=declared_empty,
        latency_ms=result.latency_ms,
        reason_codes=tuple(result.degraded_reasons),
    )


def _unavailable_result(request, profile, engine_version):
    from app.domain.knowledge.retrieval import (
        RetrievalTrace,
        SanitizedRetrievalQueryFacts,
    )

    return RetrievalResult(
        request_id=request.request_id,
        availability=RetrievalAvailability.UNAVAILABLE,
        trace=RetrievalTrace(
            request_id=request.request_id,
            profile_id=profile.profile_id,
            profile_version=profile.profile_version,
            sanitized_query_facts=SanitizedRetrievalQueryFacts(
                query_sha256=hashlib.sha256(
                    request.query_text.encode("utf-8")
                ).hexdigest(),
                character_count=len(request.query_text),
            ),
            latency_ms=0,
            latency_breakdown_ms={
                "semantic": None,
                "lexical": None,
                "fusion": None,
                "rerank": None,
                "evidence_gate": None,
                "total": 0,
            },
            degraded_reasons=["evaluation_engine_failed"],
        ),
        retrieval_engine_version=engine_version,
        profile_version=profile.profile_version,
        latency_ms=0,
        degraded_reasons=["evaluation_engine_failed"],
    )
