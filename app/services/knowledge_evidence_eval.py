from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.knowledge.evidence import EvidenceAvailability, EvidenceSufficiency
from app.services.knowledge_eval_artifacts_v3 import (
    canonical_sha256,
    write_frozen_eval_artifact,
)
from app.services.knowledge_eval_dataset_v3 import DatasetSplit


Sha256 = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
ArtifactRole = Literal["baseline", "candidate"]

EVIDENCE_METRIC_NAMES = (
    "observation_completeness_rate",
    "question_binding_precision",
    "evidence_precision_at_5",
    "expected_signal_coverage",
    "irrelevant_fallback_binding_rate",
    "targeted_supplementation_rate",
    "sufficiency_precision",
    "sufficiency_recall",
    "failure_vs_no_evidence_confusion_rate",
    "replay_stability_rate",
)
REQUIRED_MINIMUM_METRICS = {
    "observation_completeness_rate",
    "question_binding_precision",
    "evidence_precision_at_5",
    "expected_signal_coverage",
    "sufficiency_precision",
    "sufficiency_recall",
    "replay_stability_rate",
}
REQUIRED_MAXIMUM_METRICS = {
    "irrelevant_fallback_binding_rate",
    "failure_vs_no_evidence_confusion_rate",
}


class EvidenceEvalGovernance(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    annotation_protocol_version: str = Field(min_length=1)
    annotator_role: str = Field(min_length=1)
    minimum_annotators_per_case: int = Field(ge=2)
    implementation_output_blinded: Literal[True] = True
    split_frozen: Literal[True] = True
    agreement_metric: str = Field(min_length=1)
    agreement_value: float = Field(ge=-1, le=1)
    minimum_agreement: float = Field(ge=-1, le=1)
    labeling_started_at: datetime
    split_frozen_at: datetime
    provenance_record_sha256: Sha256

    @model_validator(mode="after")
    def validate_governance(self):
        for field_name in ("labeling_started_at", "split_frozen_at"):
            if getattr(self, field_name).tzinfo is None:
                raise ValueError(f"{field_name} must be timezone-aware")
        if self.split_frozen_at < self.labeling_started_at:
            raise ValueError("split cannot be frozen before labeling starts")
        if self.agreement_value < self.minimum_agreement:
            raise ValueError("evidence annotation agreement is below the minimum")
        return self


class EvidenceCalibrationCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,127}$")
    case_family: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,127}$")
    split: DatasetSplit
    topic_id: str = Field(min_length=1)
    question_input_sha256: Sha256
    relevant_evidence_ids: tuple[str, ...] = ()
    expected_signal_sha256s: tuple[Sha256, ...] = ()
    expected_availability: EvidenceAvailability
    expected_sufficiency: EvidenceSufficiency
    annotator_identity_sha256s: tuple[Sha256, ...] = Field(min_length=2)
    annotation_record_sha256s: tuple[Sha256, ...] = Field(min_length=2)
    consensus_record_sha256: Sha256

    @model_validator(mode="after")
    def validate_case(self):
        for field_name in (
            "relevant_evidence_ids",
            "expected_signal_sha256s",
            "annotator_identity_sha256s",
            "annotation_record_sha256s",
        ):
            values = getattr(self, field_name)
            if len(values) != len(set(values)):
                raise ValueError(f"{field_name} cannot contain duplicates")
        if len(self.annotator_identity_sha256s) != len(
            self.annotation_record_sha256s
        ):
            raise ValueError("annotator and annotation record counts must match")
        if self.expected_availability == EvidenceAvailability.UNAVAILABLE:
            if self.expected_sufficiency != EvidenceSufficiency.NOT_EVALUATED:
                raise ValueError("unavailable cases must use not_evaluated sufficiency")
            if self.relevant_evidence_ids or self.expected_signal_sha256s:
                raise ValueError("unavailable cases cannot declare expected evidence")
        elif self.expected_sufficiency == EvidenceSufficiency.EMPTY:
            if self.relevant_evidence_ids or self.expected_signal_sha256s:
                raise ValueError("empty cases cannot declare expected evidence")
        elif self.expected_sufficiency == EvidenceSufficiency.SUFFICIENT:
            if not self.relevant_evidence_ids or not self.expected_signal_sha256s:
                raise ValueError(
                    "sufficient cases require relevant evidence and expected signals"
                )
        return self


class EvidenceCalibrationDataset(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = "knowledge-evidence-calibration-dataset-v1"
    dataset_version: str = Field(min_length=1)
    corpus_manifest_sha256: Sha256
    governance: EvidenceEvalGovernance
    cases: tuple[EvidenceCalibrationCase, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_dataset(self):
        case_ids = [case.case_id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("evidence calibration case IDs must be unique")
        family_splits: dict[str, set[str]] = {}
        for case in self.cases:
            family_splits.setdefault(case.case_family, set()).add(case.split)
        leaked = sorted(
            family for family, splits in family_splits.items() if len(splits) > 1
        )
        if leaked:
            raise ValueError(
                "evidence case families cannot cross tuning/holdout: "
                + ", ".join(leaked)
            )
        if {case.split for case in self.cases} != {"tuning", "holdout"}:
            raise ValueError("evidence calibration requires tuning and holdout cases")
        minimum_annotators = self.governance.minimum_annotators_per_case
        incomplete = [
            case.case_id
            for case in self.cases
            if len(case.annotator_identity_sha256s) < minimum_annotators
        ]
        if incomplete:
            raise ValueError(
                "evidence cases lack required independent annotations: "
                + ", ".join(sorted(incomplete))
            )
        return self

    def validate_release_shape(
        self,
        *,
        minimum_cases: int = 30,
        maximum_cases: int = 100,
    ) -> None:
        if not minimum_cases <= len(self.cases) <= maximum_cases:
            raise ValueError(
                f"evidence calibration requires {minimum_cases}–{maximum_cases} cases"
            )
        holdout_ratio = sum(case.split == "holdout" for case in self.cases) / len(
            self.cases
        )
        if not 0.20 <= holdout_ratio <= 0.30:
            raise ValueError("evidence calibration holdout must be 20%–30%")
        if len({case.topic_id for case in self.cases}) < 2:
            raise ValueError("evidence calibration requires at least two pilot topics")
        expected_states = {
            EvidenceSufficiency.SUFFICIENT,
            EvidenceSufficiency.WEAK,
            EvidenceSufficiency.INSUFFICIENT,
            EvidenceSufficiency.EMPTY,
            EvidenceSufficiency.NOT_EVALUATED,
        }
        missing_states = expected_states - {
            case.expected_sufficiency for case in self.cases
        }
        if missing_states:
            raise ValueError(
                "evidence calibration is missing sufficiency states: "
                + ", ".join(sorted(state.value for state in missing_states))
            )
        for split in ("tuning", "holdout"):
            split_cases = [case for case in self.cases if case.split == split]
            if not any(
                case.expected_availability == EvidenceAvailability.UNAVAILABLE
                for case in split_cases
            ):
                raise ValueError(f"evidence {split} requires an unavailable case")
            if not any(
                case.expected_sufficiency == EvidenceSufficiency.EMPTY
                for case in split_cases
            ):
                raise ValueError(f"evidence {split} requires a true no-evidence case")

    def dataset_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json"))


class EvidenceEvalIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    engine_version: str = Field(min_length=1)
    retrieval_artifact_sha256: Sha256
    code_revision: str = Field(min_length=1)
    code_tree_sha256: Sha256
    selection_version: str = Field(min_length=1)
    gate_version: str = Field(min_length=1)
    corpus_manifest_sha256: Sha256


class EvidenceEvalLineageRef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_id: str = Field(min_length=1)
    content_sha256: Sha256
    corpus_manifest_sha256: Sha256


class EvidenceEvalObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str = Field(min_length=1)
    candidate_evidence_ids: tuple[str, ...] = ()
    selected_evidence_ids: tuple[str, ...] = ()
    supplemental_evidence_ids: tuple[str, ...] = ()
    final_evidence_ids: tuple[str, ...] = ()
    replayed_evidence_ids: tuple[str, ...] = ()
    final_evidence_lineage: tuple[EvidenceEvalLineageRef, ...] = ()
    covered_signal_sha256s: tuple[Sha256, ...] = ()
    availability: EvidenceAvailability
    sufficiency: EvidenceSufficiency
    reason_codes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_observation(self):
        for field_name in (
            "candidate_evidence_ids",
            "selected_evidence_ids",
            "supplemental_evidence_ids",
            "final_evidence_ids",
            "replayed_evidence_ids",
            "covered_signal_sha256s",
            "reason_codes",
        ):
            values = getattr(self, field_name)
            if len(values) != len(set(values)):
                raise ValueError(f"{field_name} cannot contain duplicates")
        lineage_ids = [item.evidence_id for item in self.final_evidence_lineage]
        if len(lineage_ids) != len(set(lineage_ids)):
            raise ValueError("final evidence lineage IDs cannot contain duplicates")
        if set(lineage_ids) != set(self.final_evidence_ids):
            raise ValueError("final evidence must have complete hash and corpus lineage")
        if not set(self.selected_evidence_ids) <= set(self.final_evidence_ids):
            raise ValueError("selected evidence must be included in final evidence")
        if not set(self.supplemental_evidence_ids) <= set(self.final_evidence_ids):
            raise ValueError("supplemental evidence must be included in final evidence")
        if self.availability == EvidenceAvailability.UNAVAILABLE:
            if self.sufficiency != EvidenceSufficiency.NOT_EVALUATED:
                raise ValueError("unavailable observations must be not_evaluated")
            if self.final_evidence_ids:
                raise ValueError("unavailable observations cannot contain final evidence")
        if self.sufficiency == EvidenceSufficiency.EMPTY and self.final_evidence_ids:
            raise ValueError("empty observations cannot contain final evidence")
        return self


class EvidenceEvalObservationBatch(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = "knowledge-evidence-observation-batch-v1"
    captured_at: datetime
    dataset_version: str
    dataset_sha256: Sha256
    split: DatasetSplit
    role: ArtifactRole
    identity: EvidenceEvalIdentity
    observations: tuple[EvidenceEvalObservation, ...]
    batch_sha256: Sha256

    @model_validator(mode="after")
    def validate_integrity(self):
        _timezone_required(self.captured_at, "observation batch captured_at")
        case_ids = [item.case_id for item in self.observations]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("observation batch case IDs must be unique")
        mismatched_lineage = sorted(
            {
                lineage.evidence_id
                for observation in self.observations
                for lineage in observation.final_evidence_lineage
                if lineage.corpus_manifest_sha256
                != self.identity.corpus_manifest_sha256
            }
        )
        if mismatched_lineage:
            raise ValueError(
                "evidence lineage has different corpus manifest: "
                + ", ".join(mismatched_lineage)
            )
        _validate_self_hash(self, "batch_sha256", "observation batch")
        return self


class EvidenceEvalMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    split: DatasetSplit
    engine_version: str
    case_count: int = Field(ge=0)
    observation_completeness_rate: float = Field(ge=0, le=1)
    question_binding_precision: float = Field(ge=0, le=1)
    evidence_precision_at_5: float = Field(ge=0, le=1)
    expected_signal_coverage: float = Field(ge=0, le=1)
    irrelevant_fallback_binding_rate: float = Field(ge=0, le=1)
    targeted_supplementation_rate: float = Field(ge=0, le=1)
    sufficiency_precision: float = Field(ge=0, le=1)
    sufficiency_recall: float = Field(ge=0, le=1)
    failure_vs_no_evidence_confusion_rate: float = Field(ge=0, le=1)
    replay_stability_rate: float = Field(ge=0, le=1)
    topic_breakdown: dict[str, dict[str, float | int]]


class EvidenceEvalCaseResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str
    candidate_evidence_ids: tuple[str, ...]
    selected_evidence_ids: tuple[str, ...]
    supplemental_evidence_ids: tuple[str, ...]
    final_evidence_ids: tuple[str, ...]
    replayed_evidence_ids: tuple[str, ...]
    final_evidence_lineage: tuple[EvidenceEvalLineageRef, ...]
    covered_signal_sha256s: tuple[Sha256, ...]
    availability: EvidenceAvailability
    sufficiency: EvidenceSufficiency
    reason_codes: tuple[str, ...]


class EvidenceEvalArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = "knowledge-evidence-eval-artifact-v1"
    role: ArtifactRole
    created_at: datetime
    dataset_version: str
    dataset_sha256: Sha256
    split: DatasetSplit
    identity: EvidenceEvalIdentity
    observation_batch_sha256: Sha256
    observations_captured_at: datetime
    metrics: EvidenceEvalMetrics
    cases: tuple[EvidenceEvalCaseResult, ...]
    threshold_registration_sha256: Sha256 | None = None
    artifact_sha256: Sha256

    @model_validator(mode="after")
    def validate_integrity(self):
        _timezone_required(self.created_at, "artifact created_at")
        _timezone_required(
            self.observations_captured_at, "artifact observations_captured_at"
        )
        if self.created_at < self.observations_captured_at:
            raise ValueError("evidence artifact cannot predate observations")
        if self.metrics.split != self.split:
            raise ValueError("evidence artifact metric split mismatch")
        if self.metrics.engine_version != self.identity.engine_version:
            raise ValueError("evidence artifact engine mismatch")
        if len(self.cases) > self.metrics.case_count:
            raise ValueError("evidence artifact has more results than dataset cases")
        if len({case.case_id for case in self.cases}) != len(self.cases):
            raise ValueError("evidence artifact result case IDs must be unique")
        if self.role == "candidate" and self.split == "holdout":
            if self.threshold_registration_sha256 is None:
                raise ValueError("candidate holdout requires threshold registration")
        if self.metrics.observation_completeness_rate != len(self.cases) / max(
            1, self.metrics.case_count
        ):
            raise ValueError("evidence artifact completeness does not match results")
        _validate_self_hash(self, "artifact_sha256", "evidence artifact")
        return self


class EvidenceEvalThresholdRegistration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = "knowledge-evidence-threshold-registration-v1"
    registered_at: datetime
    dataset_version: str
    dataset_sha256: Sha256
    corpus_manifest_sha256: Sha256
    baseline_artifact_sha256: Sha256
    candidate_identity: EvidenceEvalIdentity
    primary_metric: str
    minimum_deltas: dict[str, float]
    maximum_deltas: dict[str, float]
    absolute_minimums: dict[str, float]
    absolute_maximums: dict[str, float]
    rationale_record_sha256: Sha256
    registration_sha256: Sha256

    @model_validator(mode="after")
    def validate_integrity(self):
        _timezone_required(self.registered_at, "registration time")
        all_registered = (
            set(self.minimum_deltas)
            | set(self.maximum_deltas)
            | set(self.absolute_minimums)
            | set(self.absolute_maximums)
        )
        unknown = all_registered - set(EVIDENCE_METRIC_NAMES)
        if unknown:
            raise ValueError(
                "unknown evidence threshold metrics: " + ", ".join(sorted(unknown))
            )
        minimum_registered = set(self.minimum_deltas) | set(self.absolute_minimums)
        maximum_registered = set(self.maximum_deltas) | set(self.absolute_maximums)
        if not REQUIRED_MINIMUM_METRICS <= minimum_registered:
            raise ValueError("evidence thresholds omit required minimum metrics")
        if not REQUIRED_MAXIMUM_METRICS <= maximum_registered:
            raise ValueError("evidence thresholds omit required maximum metrics")
        if self.primary_metric not in all_registered:
            raise ValueError("primary evidence metric must be registered")
        _validate_self_hash(self, "registration_sha256", "evidence registration")
        return self


class EvidenceMetricDelta(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    metric: str
    baseline: float
    candidate: float
    delta: float


class EvidenceEvalPairedArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = "knowledge-evidence-eval-paired-v1"
    created_at: datetime
    dataset_version: str
    dataset_sha256: Sha256
    split: DatasetSplit
    baseline_artifact_sha256: Sha256
    candidate_artifact_sha256: Sha256
    registration_sha256: Sha256 | None = None
    metrics: tuple[EvidenceMetricDelta, ...]
    topic_deltas: dict[str, dict[str, float]]
    thresholds_passed: bool | None = None
    failed_thresholds: tuple[str, ...] = ()
    artifact_sha256: Sha256

    @model_validator(mode="after")
    def validate_integrity(self):
        _timezone_required(self.created_at, "paired artifact created_at")
        if self.split == "holdout":
            if self.registration_sha256 is None or self.thresholds_passed is None:
                raise ValueError("holdout comparison requires threshold decision")
        if self.thresholds_passed is True and self.failed_thresholds:
            raise ValueError("passing comparison cannot have failed thresholds")
        if self.thresholds_passed is False and not self.failed_thresholds:
            raise ValueError("failing comparison must name failed thresholds")
        _validate_self_hash(self, "artifact_sha256", "evidence paired artifact")
        return self


def load_evidence_calibration_dataset(
    path: Path | str,
    *,
    require_release_shape: bool = True,
) -> EvidenceCalibrationDataset:
    dataset = EvidenceCalibrationDataset.model_validate_json(
        Path(path).read_text(encoding="utf-8")
    )
    if require_release_shape:
        dataset.validate_release_shape()
    return dataset


def load_evidence_eval_identity(path: Path | str) -> EvidenceEvalIdentity:
    return EvidenceEvalIdentity.model_validate_json(
        Path(path).read_text(encoding="utf-8")
    )


def load_evidence_observation_batch(path: Path | str) -> EvidenceEvalObservationBatch:
    return EvidenceEvalObservationBatch.model_validate_json(
        Path(path).read_text(encoding="utf-8")
    )


def build_evidence_observation_batch(
    dataset: EvidenceCalibrationDataset,
    observations: tuple[EvidenceEvalObservation, ...],
    *,
    split: DatasetSplit,
    role: ArtifactRole,
    identity: EvidenceEvalIdentity,
    captured_at: datetime | None = None,
) -> EvidenceEvalObservationBatch:
    dataset.validate_release_shape()
    if identity.corpus_manifest_sha256 != dataset.corpus_manifest_sha256:
        raise ValueError("observation identity has different corpus manifest")
    expected_ids = {case.case_id for case in dataset.cases if case.split == split}
    case_ids = [item.case_id for item in observations]
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("evidence observations contain duplicate case IDs")
    if set(case_ids) - expected_ids:
        raise ValueError("evidence observations contain cases outside selected split")
    payload = {
        "schema_version": "knowledge-evidence-observation-batch-v1",
        "captured_at": captured_at or datetime.now(timezone.utc),
        "dataset_version": dataset.dataset_version,
        "dataset_sha256": dataset.dataset_sha256(),
        "split": split,
        "role": role,
        "identity": identity,
        "observations": observations,
    }
    return EvidenceEvalObservationBatch(
        **payload,
        batch_sha256=canonical_sha256(payload),
    )


def calculate_evidence_eval_metrics(
    dataset: EvidenceCalibrationDataset,
    observations: tuple[EvidenceEvalObservation, ...],
    *,
    split: DatasetSplit,
    engine_version: str,
) -> EvidenceEvalMetrics:
    cases = [case for case in dataset.cases if case.split == split]
    expected_ids = {case.case_id for case in cases}
    by_case: dict[str, list[EvidenceEvalObservation]] = {}
    for observation in observations:
        if observation.case_id in expected_ids:
            by_case.setdefault(observation.case_id, []).append(observation)
    complete = {
        case_id: items[0] for case_id, items in by_case.items() if len(items) == 1
    }
    values = _metric_values(cases, complete)
    topics = sorted({case.topic_id for case in cases})
    return EvidenceEvalMetrics(
        split=split,
        engine_version=engine_version,
        case_count=len(cases),
        **values,
        topic_breakdown={
            topic: {
                "case_count": len(topic_cases),
                **_metric_values(topic_cases, complete),
            }
            for topic in topics
            if (topic_cases := [case for case in cases if case.topic_id == topic])
        },
    )


def build_evidence_eval_artifact(
    dataset: EvidenceCalibrationDataset,
    observation_batch: EvidenceEvalObservationBatch,
    *,
    registration: EvidenceEvalThresholdRegistration | None = None,
    created_at: datetime | None = None,
) -> EvidenceEvalArtifact:
    dataset.validate_release_shape()
    split = observation_batch.split
    role = observation_batch.role
    identity = observation_batch.identity
    observations = observation_batch.observations
    if observation_batch.dataset_version != dataset.dataset_version:
        raise ValueError("observation batch has different dataset version")
    if observation_batch.dataset_sha256 != dataset.dataset_sha256():
        raise ValueError("observation batch has different dataset hash")
    if identity.corpus_manifest_sha256 != dataset.corpus_manifest_sha256:
        raise ValueError("evidence identity has different corpus manifest")
    timestamp = created_at or datetime.now(timezone.utc)
    if timestamp < observation_batch.captured_at:
        raise ValueError("evidence artifact cannot predate observation batch")
    if role == "candidate" and split == "holdout":
        if registration is None:
            raise ValueError("candidate holdout requires pre-registered thresholds")
        _validate_candidate_registration(dataset, identity, registration)
        if registration.registered_at >= observation_batch.captured_at:
            raise ValueError("thresholds must predate candidate holdout observations")
    elif registration is not None:
        raise ValueError("threshold registration is only accepted for candidate holdout")
    cases = [case for case in dataset.cases if case.split == split]
    observation_by_id = {item.case_id: item for item in observations}
    if len(observation_by_id) != len(observations):
        raise ValueError("evidence observations contain duplicate case IDs")
    expected_ids = {case.case_id for case in cases}
    if set(observation_by_id) - expected_ids:
        raise ValueError("evidence observations contain cases outside the selected split")
    metrics = calculate_evidence_eval_metrics(
        dataset,
        observations,
        split=split,
        engine_version=identity.engine_version,
    )
    results = tuple(
        EvidenceEvalCaseResult(**observation_by_id[case.case_id].model_dump())
        for case in cases
        if case.case_id in observation_by_id
    )
    payload = {
        "schema_version": "knowledge-evidence-eval-artifact-v1",
        "role": role,
        "created_at": timestamp,
        "dataset_version": dataset.dataset_version,
        "dataset_sha256": dataset.dataset_sha256(),
        "split": split,
        "identity": identity,
        "observation_batch_sha256": observation_batch.batch_sha256,
        "observations_captured_at": observation_batch.captured_at,
        "metrics": metrics,
        "cases": results,
        "threshold_registration_sha256": (
            registration.registration_sha256 if registration is not None else None
        ),
    }
    return EvidenceEvalArtifact(
        **payload,
        artifact_sha256=canonical_sha256(payload),
    )


def build_evidence_threshold_registration(
    baseline: EvidenceEvalArtifact,
    *,
    candidate_identity: EvidenceEvalIdentity,
    primary_metric: str,
    minimum_deltas: dict[str, float],
    maximum_deltas: dict[str, float],
    absolute_minimums: dict[str, float],
    absolute_maximums: dict[str, float],
    rationale_record_sha256: str,
    registered_at: datetime | None = None,
) -> EvidenceEvalThresholdRegistration:
    if baseline.role != "baseline" or baseline.split != "holdout":
        raise ValueError("evidence registration requires a holdout baseline artifact")
    if baseline.metrics.observation_completeness_rate != 1.0:
        raise ValueError("evidence baseline observation completeness must be 100%")
    if baseline.metrics.replay_stability_rate != 1.0:
        raise ValueError("evidence baseline replay stability must be 100%")
    if candidate_identity.corpus_manifest_sha256 != baseline.identity.corpus_manifest_sha256:
        raise ValueError("candidate identity has different corpus manifest")
    timestamp = registered_at or datetime.now(timezone.utc)
    if timestamp <= baseline.created_at:
        raise ValueError("evidence thresholds must be registered after baseline")
    payload = {
        "schema_version": "knowledge-evidence-threshold-registration-v1",
        "registered_at": timestamp,
        "dataset_version": baseline.dataset_version,
        "dataset_sha256": baseline.dataset_sha256,
        "corpus_manifest_sha256": baseline.identity.corpus_manifest_sha256,
        "baseline_artifact_sha256": baseline.artifact_sha256,
        "candidate_identity": candidate_identity,
        "primary_metric": primary_metric,
        "minimum_deltas": minimum_deltas,
        "maximum_deltas": maximum_deltas,
        "absolute_minimums": absolute_minimums,
        "absolute_maximums": absolute_maximums,
        "rationale_record_sha256": rationale_record_sha256,
    }
    return EvidenceEvalThresholdRegistration(
        **payload,
        registration_sha256=canonical_sha256(payload),
    )


def compare_evidence_eval_artifacts(
    baseline: EvidenceEvalArtifact,
    candidate: EvidenceEvalArtifact,
    *,
    registration: EvidenceEvalThresholdRegistration | None = None,
    created_at: datetime | None = None,
) -> EvidenceEvalPairedArtifact:
    if baseline.role != "baseline" or candidate.role != "candidate":
        raise ValueError("evidence comparison requires baseline and candidate roles")
    for field_name in ("dataset_version", "dataset_sha256", "split"):
        if getattr(baseline, field_name) != getattr(candidate, field_name):
            raise ValueError(f"evidence artifacts have different {field_name}")
    if baseline.identity.corpus_manifest_sha256 != candidate.identity.corpus_manifest_sha256:
        raise ValueError("evidence artifacts have different corpus manifests")
    if tuple(case.case_id for case in baseline.cases) != tuple(
        case.case_id for case in candidate.cases
    ):
        raise ValueError("evidence artifacts require identical ordered case IDs")
    if baseline.identity.engine_version == candidate.identity.engine_version:
        raise ValueError("evidence comparison requires different engines")
    if baseline.metrics.observation_completeness_rate != 1.0:
        raise ValueError("baseline evidence observation completeness must be 100%")
    if candidate.metrics.observation_completeness_rate != 1.0:
        raise ValueError("candidate evidence observation completeness must be 100%")
    if baseline.metrics.replay_stability_rate != 1.0:
        raise ValueError("baseline evidence replay stability must be 100%")
    if candidate.metrics.replay_stability_rate != 1.0:
        raise ValueError("candidate evidence replay stability must be 100%")
    if baseline.split == "holdout":
        if registration is None:
            raise ValueError("holdout evidence comparison requires registration")
        _validate_comparison_registration(baseline, candidate, registration)
    metrics = tuple(
        EvidenceMetricDelta(
            metric=name,
            baseline=float(getattr(baseline.metrics, name)),
            candidate=float(getattr(candidate.metrics, name)),
            delta=float(getattr(candidate.metrics, name) - getattr(baseline.metrics, name)),
        )
        for name in EVIDENCE_METRIC_NAMES
    )
    topic_deltas = _topic_deltas(
        baseline.metrics.topic_breakdown,
        candidate.metrics.topic_breakdown,
    )
    failures = (
        _failed_thresholds(metrics, registration) if registration is not None else ()
    )
    timestamp = created_at or datetime.now(timezone.utc)
    if timestamp < max(baseline.created_at, candidate.created_at):
        raise ValueError("evidence comparison cannot predate source artifacts")
    payload = {
        "schema_version": "knowledge-evidence-eval-paired-v1",
        "created_at": timestamp,
        "dataset_version": baseline.dataset_version,
        "dataset_sha256": baseline.dataset_sha256,
        "split": baseline.split,
        "baseline_artifact_sha256": baseline.artifact_sha256,
        "candidate_artifact_sha256": candidate.artifact_sha256,
        "registration_sha256": (
            registration.registration_sha256 if registration is not None else None
        ),
        "metrics": metrics,
        "topic_deltas": topic_deltas,
        "thresholds_passed": not failures if registration is not None else None,
        "failed_thresholds": failures,
    }
    return EvidenceEvalPairedArtifact(
        **payload,
        artifact_sha256=canonical_sha256(payload),
    )


def load_evidence_eval_artifact(path: Path | str) -> EvidenceEvalArtifact:
    return EvidenceEvalArtifact.model_validate_json(
        Path(path).read_text(encoding="utf-8")
    )


def load_evidence_threshold_registration(
    path: Path | str,
) -> EvidenceEvalThresholdRegistration:
    return EvidenceEvalThresholdRegistration.model_validate_json(
        Path(path).read_text(encoding="utf-8")
    )


def write_evidence_eval_artifact(artifact: BaseModel, path: Path | str) -> Path:
    return write_frozen_eval_artifact(artifact, path)


def _metric_values(cases, observations) -> dict[str, float]:
    selected_total = selected_relevant = 0
    candidate_total = candidate_relevant = 0
    expected_signals = covered_signals = 0
    irrelevant_fallbacks = supplementations = 0
    sufficient_true_positive = predicted_sufficient = gold_sufficient = 0
    confusion_cases = confused = 0
    replayed = 0
    for case in cases:
        observation = observations.get(case.case_id)
        if observation is None:
            continue
        relevant = set(case.relevant_evidence_ids)
        selected = set(observation.selected_evidence_ids)
        candidates = observation.candidate_evidence_ids[:5]
        selected_total += len(selected)
        selected_relevant += len(selected & relevant)
        candidate_total += len(candidates)
        candidate_relevant += sum(item in relevant for item in candidates)
        expected = set(case.expected_signal_sha256s)
        expected_signals += len(expected)
        covered_signals += len(expected & set(observation.covered_signal_sha256s))
        irrelevant_fallbacks += int(bool(selected) and not bool(selected & relevant))
        supplementations += int(bool(observation.supplemental_evidence_ids))
        gold_is_sufficient = case.expected_sufficiency == EvidenceSufficiency.SUFFICIENT
        predicted_is_sufficient = observation.sufficiency == EvidenceSufficiency.SUFFICIENT
        gold_sufficient += int(gold_is_sufficient)
        predicted_sufficient += int(predicted_is_sufficient)
        sufficient_true_positive += int(gold_is_sufficient and predicted_is_sufficient)
        gold_failure = case.expected_availability == EvidenceAvailability.UNAVAILABLE
        gold_empty = (
            case.expected_availability != EvidenceAvailability.UNAVAILABLE
            and case.expected_sufficiency == EvidenceSufficiency.EMPTY
        )
        if gold_failure or gold_empty:
            confusion_cases += 1
            predicted_failure = observation.availability == EvidenceAvailability.UNAVAILABLE
            predicted_empty = (
                observation.availability != EvidenceAvailability.UNAVAILABLE
                and observation.sufficiency == EvidenceSufficiency.EMPTY
            )
            confused += int(
                (gold_failure and predicted_empty) or (gold_empty and predicted_failure)
            )
        replayed += int(
            set(observation.final_evidence_ids)
            <= set(observation.replayed_evidence_ids)
        )
    observed_count = sum(case.case_id in observations for case in cases)
    return {
        "observation_completeness_rate": _ratio(observed_count, len(cases)),
        "question_binding_precision": (
            _ratio(selected_relevant, selected_total) if selected_total else 1.0
        ),
        "evidence_precision_at_5": (
            _ratio(candidate_relevant, candidate_total) if candidate_total else 1.0
        ),
        "expected_signal_coverage": (
            _ratio(covered_signals, expected_signals) if expected_signals else 1.0
        ),
        "irrelevant_fallback_binding_rate": _ratio(
            irrelevant_fallbacks, len(cases)
        ),
        "targeted_supplementation_rate": _ratio(supplementations, len(cases)),
        "sufficiency_precision": (
            _ratio(sufficient_true_positive, predicted_sufficient)
            if predicted_sufficient
            else 1.0 if not gold_sufficient else 0.0
        ),
        "sufficiency_recall": (
            _ratio(sufficient_true_positive, gold_sufficient)
            if gold_sufficient
            else 1.0
        ),
        "failure_vs_no_evidence_confusion_rate": _ratio(
            confused, confusion_cases
        ),
        "replay_stability_rate": _ratio(replayed, len(cases)),
    }


def _validate_candidate_registration(dataset, identity, registration) -> None:
    expected = {
        "dataset_version": dataset.dataset_version,
        "dataset_sha256": dataset.dataset_sha256(),
        "corpus_manifest_sha256": dataset.corpus_manifest_sha256,
        "candidate_identity": identity,
    }
    for field_name, value in expected.items():
        if getattr(registration, field_name) != value:
            raise ValueError(f"evidence registration has different {field_name}")


def _validate_comparison_registration(baseline, candidate, registration) -> None:
    expected = {
        "dataset_version": baseline.dataset_version,
        "dataset_sha256": baseline.dataset_sha256,
        "corpus_manifest_sha256": baseline.identity.corpus_manifest_sha256,
        "baseline_artifact_sha256": baseline.artifact_sha256,
        "candidate_identity": candidate.identity,
    }
    for field_name, value in expected.items():
        if getattr(registration, field_name) != value:
            raise ValueError(f"evidence registration has different {field_name}")
    if registration.registered_at <= baseline.created_at:
        raise ValueError("evidence thresholds must be after baseline")
    if registration.registered_at >= candidate.observations_captured_at:
        raise ValueError("evidence thresholds must predate candidate observations")
    if candidate.threshold_registration_sha256 != registration.registration_sha256:
        raise ValueError("candidate artifact does not bind threshold registration")


def _failed_thresholds(metrics, registration) -> tuple[str, ...]:
    by_name = {metric.metric: metric for metric in metrics}
    failures = []
    for name, value in registration.minimum_deltas.items():
        if by_name[name].delta < value:
            failures.append(f"minimum_delta:{name}")
    for name, value in registration.maximum_deltas.items():
        if by_name[name].delta > value:
            failures.append(f"maximum_delta:{name}")
    for name, value in registration.absolute_minimums.items():
        if by_name[name].candidate < value:
            failures.append(f"absolute_minimum:{name}")
    for name, value in registration.absolute_maximums.items():
        if by_name[name].candidate > value:
            failures.append(f"absolute_maximum:{name}")
    return tuple(sorted(failures))


def _topic_deltas(baseline, candidate) -> dict[str, dict[str, float]]:
    if set(baseline) != set(candidate):
        raise ValueError("evidence comparison requires identical topic breakdowns")
    result = {}
    for topic in sorted(baseline):
        if set(baseline[topic]) != set(candidate[topic]):
            raise ValueError(f"evidence topic fields differ for {topic}")
        result[topic] = {
            name: float(candidate[topic][name]) - float(baseline[topic][name])
            for name in sorted(baseline[topic])
        }
    return result


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _timezone_required(value: datetime, label: str) -> None:
    if value.tzinfo is None:
        raise ValueError(f"{label} must be timezone-aware")


def _validate_self_hash(model: BaseModel, field_name: str, label: str) -> None:
    payload = model.model_dump(mode="json", exclude={field_name})
    if canonical_sha256(payload) != getattr(model, field_name):
        raise ValueError(f"{label} SHA-256 mismatch")
