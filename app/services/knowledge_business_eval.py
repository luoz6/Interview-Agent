from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.services.knowledge_eval_artifacts_v3 import (
    canonical_sha256,
    write_frozen_eval_artifact,
)


BusinessEvalTarget = Literal["followup", "reviewer"]
DatasetSplit = Literal["tuning", "holdout"]
BlindLabel = Literal["A", "B"]
ScenarioType = Literal[
    "strong_answer",
    "partial_answer",
    "typical_error",
    "misunderstood_question",
    "skipped_or_empty",
    "terminology_stacking",
    "factual_hallucination",
    "cross_domain_answer",
]
EvidenceAvailability = Literal["available", "degraded", "unavailable"]
EvidenceSufficiency = Literal[
    "sufficient", "weak", "insufficient", "empty", "not_evaluated"
]

FOLLOWUP_POSITIVE_DIMENSIONS = (
    "answer_specificity",
    "missing_or_incorrect_signal_targeting",
    "depth_gain",
    "role_seniority_relevance",
    "evidence_grounding",
)
FOLLOWUP_NEGATIVE_DIMENSIONS = (
    "repetition",
    "over_leading",
    "unsupported_technical_claim",
)
REVIEWER_POSITIVE_DIMENSIONS = (
    "expert_agreement",
    "score_stability",
    "evidence_support",
    "confidence_calibration",
    "no_evidence_handling",
    "system_failure_handling",
)
REVIEWER_NEGATIVE_DIMENSIONS = (
    "unsupported_judgment",
    "repeated_evaluation_variance",
)

POSITIVE_DIMENSIONS = {
    "followup": FOLLOWUP_POSITIVE_DIMENSIONS,
    "reviewer": REVIEWER_POSITIVE_DIMENSIONS,
}
NEGATIVE_DIMENSIONS = {
    "followup": FOLLOWUP_NEGATIVE_DIMENSIONS,
    "reviewer": REVIEWER_NEGATIVE_DIMENSIONS,
}
ALL_DIMENSIONS = {
    target: (*POSITIVE_DIMENSIONS[target], *NEGATIVE_DIMENSIONS[target])
    for target in ("followup", "reviewer")
}


class BusinessEvalEngineIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    engine_id: str = Field(min_length=1)
    engine_version: str = Field(min_length=1)
    code_revision: str = Field(min_length=1)
    code_tree_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    profile_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class BusinessEvalOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str = Field(min_length=1)
    score: float | None = Field(default=None, ge=0, le=100)
    repeated_scores: tuple[float, ...] = ()
    confidence: Literal["high", "medium", "low", "not_scorable"] | None = None
    evidence_ids: tuple[str, ...] = ()
    system_failure: bool = False

    @model_validator(mode="after")
    def validate_repeated_scores(self):
        if any(not 0 <= score <= 100 for score in self.repeated_scores):
            raise ValueError("repeated scores must be between 0 and 100")
        return self


class BusinessEvalCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str = Field(min_length=1)
    case_family: str = Field(min_length=1)
    split: DatasetSplit
    target: BusinessEvalTarget
    scenario_type: ScenarioType
    role: str = Field(min_length=1)
    seniority: str = Field(min_length=1)
    question: str = Field(min_length=1)
    candidate_answer: str
    evidence_ids: tuple[str, ...] = ()
    evidence_availability: EvidenceAvailability = "available"
    evidence_sufficiency: EvidenceSufficiency = "sufficient"
    system_failure_scenario: bool = False
    baseline_output: BusinessEvalOutput
    candidate_output: BusinessEvalOutput

    @model_validator(mode="after")
    def validate_case(self):
        if not self.candidate_answer.strip() and self.scenario_type != "skipped_or_empty":
            raise ValueError("only skipped_or_empty cases may have a blank answer")
        if self.target == "followup":
            for output in (self.baseline_output, self.candidate_output):
                if (
                    output.score is not None
                    or output.confidence is not None
                    or output.repeated_scores
                ):
                    raise ValueError("followup outputs cannot contain reviewer scores")
        return self

    def source_input_sha256(self) -> str:
        return canonical_sha256(
            {
                "case_id": self.case_id,
                "role": self.role,
                "seniority": self.seniority,
                "question": self.question,
                "candidate_answer": self.candidate_answer,
                "evidence_ids": self.evidence_ids,
                "evidence_availability": self.evidence_availability,
                "evidence_sufficiency": self.evidence_sufficiency,
                "system_failure_scenario": self.system_failure_scenario,
            }
        )


class BusinessEvalGovernance(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    protocol_version: str = Field(min_length=1)
    split_frozen: bool
    outputs_frozen: bool
    randomized_blind_ab: bool
    minimum_annotators_per_case: int = Field(ge=2)
    annotator_roles: tuple[str, ...] = Field(min_length=1)
    minimum_qualification: str = Field(min_length=1)
    adjudication_rule: str = Field(min_length=1)
    agreement_metric: str = Field(min_length=1)
    minimum_agreement: float = Field(ge=-1, le=1)
    frozen_at: datetime
    provenance_record_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def validate_governance(self):
        if self.frozen_at.tzinfo is None:
            raise ValueError("governance frozen_at must be timezone-aware")
        return self


class KnowledgeBusinessEvalDataset(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = "knowledge-business-eval-dataset-v1"
    dataset_version: str = Field(min_length=1)
    baseline_identity: BusinessEvalEngineIdentity
    candidate_identity: BusinessEvalEngineIdentity
    governance: BusinessEvalGovernance
    cases: tuple[BusinessEvalCase, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_dataset(self):
        if self.baseline_identity.engine_id == self.candidate_identity.engine_id:
            raise ValueError("baseline and candidate engine IDs must differ")
        case_ids = [case.case_id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("business eval case IDs must be unique")
        family_splits: dict[str, set[str]] = {}
        for case in self.cases:
            family_splits.setdefault(case.case_family, set()).add(case.split)
        leaked = sorted(
            family for family, splits in family_splits.items() if len(splits) > 1
        )
        if leaked:
            raise ValueError(
                "business eval case families cannot cross tuning/holdout: "
                + ", ".join(leaked)
            )
        if {case.split for case in self.cases} != {"tuning", "holdout"}:
            raise ValueError("business eval dataset requires tuning and holdout cases")
        return self

    def validate_release_shape(
        self,
        *,
        minimum_cases: int = 50,
        maximum_cases: int = 100,
    ) -> None:
        if not minimum_cases <= len(self.cases) <= maximum_cases:
            raise ValueError(
                f"business eval release dataset requires {minimum_cases}–"
                f"{maximum_cases} cases"
            )
        if not (
            self.governance.split_frozen
            and self.governance.outputs_frozen
            and self.governance.randomized_blind_ab
        ):
            raise ValueError("business eval release data must be frozen and blinded")
        holdout_ratio = sum(case.split == "holdout" for case in self.cases) / len(
            self.cases
        )
        if not 0.20 <= holdout_ratio <= 0.30:
            raise ValueError("business eval holdout ratio must be between 20% and 30%")
        missing_targets = sorted(
            {"followup", "reviewer"} - {case.target for case in self.cases}
        )
        if missing_targets:
            raise ValueError(
                "business eval dataset is missing targets: " + ", ".join(missing_targets)
            )
        for split in ("tuning", "holdout"):
            split_targets = {case.target for case in self.cases if case.split == split}
            missing_split_targets = sorted({"followup", "reviewer"} - split_targets)
            if missing_split_targets:
                raise ValueError(
                    f"business eval {split} split is missing targets: "
                    + ", ".join(missing_split_targets)
                )
        missing_scenarios = sorted(
            set(ScenarioType.__args__) - {case.scenario_type for case in self.cases}
        )
        if missing_scenarios:
            raise ValueError(
                "business eval dataset is missing scenarios: "
                + ", ".join(missing_scenarios)
            )
        reviewer_cases = [case for case in self.cases if case.target == "reviewer"]
        if not any(
            case.evidence_availability == "unavailable" for case in reviewer_cases
        ):
            raise ValueError("business eval requires a Reviewer unavailable case")
        if not any(
            case.evidence_sufficiency in {"empty", "insufficient"}
            for case in reviewer_cases
        ):
            raise ValueError(
                "business eval requires a Reviewer no-evidence/insufficient case"
            )
        if not any(case.system_failure_scenario for case in reviewer_cases):
            raise ValueError("business eval requires a Reviewer system-failure case")
        if any(
            len(output.repeated_scores) < 2
            for case in reviewer_cases
            for output in (case.baseline_output, case.candidate_output)
        ):
            raise ValueError(
                "business eval Reviewer outputs require at least two repeated scores"
            )

    def dataset_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json"))


class BlindBusinessEvalOption(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    label: BlindLabel
    output: BusinessEvalOutput


class BlindBusinessEvalCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str
    target: BusinessEvalTarget
    scenario_type: ScenarioType
    role: str
    seniority: str
    question: str
    candidate_answer: str
    evidence_ids: tuple[str, ...] = ()
    evidence_availability: EvidenceAvailability
    evidence_sufficiency: EvidenceSufficiency
    system_failure_scenario: bool
    source_input_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    options: tuple[BlindBusinessEvalOption, BlindBusinessEvalOption]

    @model_validator(mode="after")
    def validate_options(self):
        if {option.label for option in self.options} != {"A", "B"}:
            raise ValueError("blind case requires exactly A and B options")
        return self


class BlindBusinessEvalPackage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = "knowledge-business-blind-package-v1"
    created_at: datetime
    dataset_version: str
    dataset_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    split: DatasetSplit
    restricted_contains_raw_interview_content: Literal[True] = True
    cases: tuple[BlindBusinessEvalCase, ...]
    package_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def validate_integrity(self):
        _validate_timestamp(self.created_at, "package created_at")
        _validate_unique_case_ids(self.cases, "blind package")
        _validate_self_hash(self, "package_sha256", "blind package")
        return self


class BlindMappingCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str
    A: str = Field(min_length=1)
    B: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_mapping(self):
        if self.A == self.B:
            raise ValueError("blind mapping requires different engines")
        return self


class BlindBusinessEvalMapping(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = "knowledge-business-blind-mapping-v1"
    created_at: datetime
    dataset_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    package_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    seed_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    restricted_unblinding_key: Literal[True] = True
    cases: tuple[BlindMappingCase, ...]
    mapping_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def validate_integrity(self):
        _validate_timestamp(self.created_at, "mapping created_at")
        _validate_unique_case_ids(self.cases, "blind mapping")
        _validate_self_hash(self, "mapping_sha256", "blind mapping")
        return self


class BusinessEvalRatings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    A: dict[str, float]
    B: dict[str, float]

    @model_validator(mode="after")
    def validate_values(self):
        for label, ratings in (("A", self.A), ("B", self.B)):
            if any(not 0 <= value <= 1 for value in ratings.values()):
                raise ValueError(f"{label} ratings must be between 0 and 1")
        return self


class BusinessEvalAnnotationRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str
    annotator_identity_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    ratings: BusinessEvalRatings
    annotation_record_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def validate_integrity(self):
        _validate_self_hash(self, "annotation_record_sha256", "annotation record")
        return self


class BusinessEvalConsensusRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str
    adjudicator_identity_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_annotation_record_sha256s: tuple[str, ...] = Field(min_length=2)
    ratings: BusinessEvalRatings
    consensus_record_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def validate_integrity(self):
        if len(set(self.source_annotation_record_sha256s)) != len(
            self.source_annotation_record_sha256s
        ):
            raise ValueError("consensus source annotation hashes must be unique")
        _validate_self_hash(self, "consensus_record_sha256", "consensus record")
        return self


class BusinessEvalAnnotationGovernance(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    protocol_version: str = Field(min_length=1)
    annotator_roles: tuple[str, ...] = Field(min_length=1)
    minimum_qualification: str = Field(min_length=1)
    minimum_annotators_per_case: int = Field(ge=2)
    blinded: Literal[True] = True
    adjudication_rule: str = Field(min_length=1)
    agreement_metric: str = Field(min_length=1)
    agreement_value: float = Field(ge=-1, le=1)
    minimum_agreement: float = Field(ge=-1, le=1)
    collection_started_at: datetime
    collection_completed_at: datetime

    @model_validator(mode="after")
    def validate_governance(self):
        _validate_timestamp(self.collection_started_at, "collection_started_at")
        _validate_timestamp(self.collection_completed_at, "collection_completed_at")
        if self.collection_completed_at < self.collection_started_at:
            raise ValueError("annotation collection cannot finish before it starts")
        if self.agreement_value < self.minimum_agreement:
            raise ValueError("observed agreement is below the registered minimum")
        return self


class BusinessEvalAnnotationSet(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = "knowledge-business-annotations-v1"
    dataset_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    package_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    split: DatasetSplit
    governance: BusinessEvalAnnotationGovernance
    records: tuple[BusinessEvalAnnotationRecord, ...]
    consensus: tuple[BusinessEvalConsensusRecord, ...]


class BusinessEvalTargetThresholds(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    primary_metric: str = Field(min_length=1)
    minimum_deltas: dict[str, float]
    maximum_deltas: dict[str, float]


class BusinessEvalThresholdRegistration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = "knowledge-business-threshold-registration-v1"
    registered_at: datetime
    dataset_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    package_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    mapping_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    baseline_identity: BusinessEvalEngineIdentity
    candidate_identity: BusinessEvalEngineIdentity
    target_thresholds: dict[BusinessEvalTarget, BusinessEvalTargetThresholds]
    rationale_record_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    registration_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def validate_integrity(self):
        _validate_timestamp(self.registered_at, "registered_at")
        _validate_thresholds(self.target_thresholds)
        _validate_self_hash(self, "registration_sha256", "threshold registration")
        return self


class BusinessEvalMetricResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    baseline: float
    candidate: float
    delta: float
    candidate_wins: int = Field(ge=0)
    ties: int = Field(ge=0)
    candidate_losses: int = Field(ge=0)


class BusinessEvalCaseResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str
    target: BusinessEvalTarget
    source_input_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    baseline_ratings: dict[str, float]
    candidate_ratings: dict[str, float]
    annotation_record_sha256s: tuple[str, ...]
    consensus_record_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class BusinessEvalResultArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = "knowledge-business-eval-result-v1"
    created_at: datetime
    dataset_version: str
    dataset_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    split: DatasetSplit
    package_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    mapping_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    registration_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    baseline_identity: BusinessEvalEngineIdentity
    candidate_identity: BusinessEvalEngineIdentity
    agreement_metric: str
    agreement_value: float
    annotation_set_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    metrics: dict[BusinessEvalTarget, dict[str, BusinessEvalMetricResult]]
    cases: tuple[BusinessEvalCaseResult, ...]
    thresholds_passed: bool | None = None
    failed_thresholds: tuple[str, ...] = ()
    artifact_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def validate_integrity(self):
        _validate_timestamp(self.created_at, "result created_at")
        if self.split == "holdout" and self.registration_sha256 is None:
            raise ValueError("holdout result requires threshold registration")
        if self.split == "holdout" and self.thresholds_passed is None:
            raise ValueError("holdout result requires a threshold decision")
        if self.thresholds_passed is True and self.failed_thresholds:
            raise ValueError("passing result cannot contain failed thresholds")
        if self.thresholds_passed is False and not self.failed_thresholds:
            raise ValueError("failing result must identify failed thresholds")
        _validate_self_hash(self, "artifact_sha256", "business eval result")
        return self


def load_business_eval_dataset(path: Path | str) -> KnowledgeBusinessEvalDataset:
    return KnowledgeBusinessEvalDataset.model_validate_json(
        Path(path).read_text(encoding="utf-8")
    )


def build_blind_business_eval_package(
    dataset: KnowledgeBusinessEvalDataset,
    *,
    split: DatasetSplit,
    seed: str,
    created_at: datetime | None = None,
) -> tuple[BlindBusinessEvalPackage, BlindBusinessEvalMapping]:
    if not seed:
        raise ValueError("blind randomization seed must not be empty")
    timestamp = created_at or datetime.now(timezone.utc)
    cases = [case for case in dataset.cases if case.split == split]
    if not cases:
        raise ValueError(f"dataset has no {split} cases")
    cases.sort(key=lambda case: canonical_sha256([seed, "order", case.case_id]))
    blind_cases = []
    mappings = []
    for case in cases:
        baseline_first = int(
            canonical_sha256([seed, "option", case.case_id]), 16
        ) % 2 == 0
        outputs = (
            (case.baseline_output, case.candidate_output)
            if baseline_first
            else (case.candidate_output, case.baseline_output)
        )
        engine_ids = (
            (dataset.baseline_identity.engine_id, dataset.candidate_identity.engine_id)
            if baseline_first
            else (dataset.candidate_identity.engine_id, dataset.baseline_identity.engine_id)
        )
        blind_cases.append(
            BlindBusinessEvalCase(
                case_id=case.case_id,
                target=case.target,
                scenario_type=case.scenario_type,
                role=case.role,
                seniority=case.seniority,
                question=case.question,
                candidate_answer=case.candidate_answer,
                evidence_ids=case.evidence_ids,
                evidence_availability=case.evidence_availability,
                evidence_sufficiency=case.evidence_sufficiency,
                system_failure_scenario=case.system_failure_scenario,
                source_input_sha256=case.source_input_sha256(),
                options=(
                    BlindBusinessEvalOption(label="A", output=outputs[0]),
                    BlindBusinessEvalOption(label="B", output=outputs[1]),
                ),
            )
        )
        mappings.append(
            BlindMappingCase(case_id=case.case_id, A=engine_ids[0], B=engine_ids[1])
        )
    package_payload = {
        "schema_version": "knowledge-business-blind-package-v1",
        "created_at": timestamp,
        "dataset_version": dataset.dataset_version,
        "dataset_sha256": dataset.dataset_sha256(),
        "split": split,
        "restricted_contains_raw_interview_content": True,
        "cases": tuple(blind_cases),
    }
    package = BlindBusinessEvalPackage(
        **package_payload,
        package_sha256=canonical_sha256(package_payload),
    )
    mapping_payload = {
        "schema_version": "knowledge-business-blind-mapping-v1",
        "created_at": timestamp,
        "dataset_sha256": dataset.dataset_sha256(),
        "package_sha256": package.package_sha256,
        "seed_sha256": canonical_sha256(seed),
        "restricted_unblinding_key": True,
        "cases": tuple(mappings),
    }
    mapping = BlindBusinessEvalMapping(
        **mapping_payload,
        mapping_sha256=canonical_sha256(mapping_payload),
    )
    return package, mapping


def build_business_eval_threshold_registration(
    dataset: KnowledgeBusinessEvalDataset,
    package: BlindBusinessEvalPackage,
    mapping: BlindBusinessEvalMapping,
    *,
    target_thresholds: dict[BusinessEvalTarget, BusinessEvalTargetThresholds],
    rationale_record_sha256: str,
    registered_at: datetime | None = None,
) -> BusinessEvalThresholdRegistration:
    dataset.validate_release_shape()
    _validate_package_mapping(dataset, package, mapping)
    timestamp = registered_at or datetime.now(timezone.utc)
    if package.split != "holdout":
        raise ValueError("threshold registration is only valid for holdout")
    if timestamp <= package.created_at:
        raise ValueError("threshold registration must be after blind package creation")
    payload = {
        "schema_version": "knowledge-business-threshold-registration-v1",
        "registered_at": timestamp,
        "dataset_sha256": dataset.dataset_sha256(),
        "package_sha256": package.package_sha256,
        "mapping_sha256": mapping.mapping_sha256,
        "baseline_identity": dataset.baseline_identity,
        "candidate_identity": dataset.candidate_identity,
        "target_thresholds": target_thresholds,
        "rationale_record_sha256": rationale_record_sha256,
    }
    return BusinessEvalThresholdRegistration(
        **payload,
        registration_sha256=canonical_sha256(payload),
    )


def build_business_annotation_record(
    *,
    case_id: str,
    annotator_identity_sha256: str,
    ratings: BusinessEvalRatings,
) -> BusinessEvalAnnotationRecord:
    payload = {
        "case_id": case_id,
        "annotator_identity_sha256": annotator_identity_sha256,
        "ratings": ratings,
    }
    return BusinessEvalAnnotationRecord(
        **payload,
        annotation_record_sha256=canonical_sha256(payload),
    )


def build_business_consensus_record(
    *,
    case_id: str,
    adjudicator_identity_sha256: str,
    source_annotation_record_sha256s: tuple[str, ...],
    ratings: BusinessEvalRatings,
) -> BusinessEvalConsensusRecord:
    payload = {
        "case_id": case_id,
        "adjudicator_identity_sha256": adjudicator_identity_sha256,
        "source_annotation_record_sha256s": source_annotation_record_sha256s,
        "ratings": ratings,
    }
    return BusinessEvalConsensusRecord(
        **payload,
        consensus_record_sha256=canonical_sha256(payload),
    )


def compare_blind_business_eval(
    dataset: KnowledgeBusinessEvalDataset,
    package: BlindBusinessEvalPackage,
    mapping: BlindBusinessEvalMapping,
    annotations: BusinessEvalAnnotationSet,
    *,
    registration: BusinessEvalThresholdRegistration | None = None,
    created_at: datetime | None = None,
) -> BusinessEvalResultArtifact:
    _validate_package_mapping(dataset, package, mapping)
    if package.split == "holdout":
        dataset.validate_release_shape()
    _validate_annotations(dataset, package, annotations)
    if package.split == "holdout":
        if registration is None:
            raise ValueError("holdout comparison requires threshold registration")
        _validate_registration(dataset, package, mapping, annotations, registration)
    elif registration is not None:
        raise ValueError("tuning comparison cannot use holdout threshold registration")

    result_created_at = created_at or datetime.now(timezone.utc)
    if result_created_at <= annotations.governance.collection_completed_at:
        raise ValueError("business eval result must be created after annotation completes")

    mapping_by_case = {case.case_id: case for case in mapping.cases}
    package_by_case = {case.case_id: case for case in package.cases}
    records_by_case: dict[str, list[BusinessEvalAnnotationRecord]] = {}
    for record in annotations.records:
        records_by_case.setdefault(record.case_id, []).append(record)
    consensus_by_case = {record.case_id: record for record in annotations.consensus}
    case_results = []
    aggregate: dict[str, dict[str, list[tuple[float, float]]]] = {}
    for case_id in [case.case_id for case in package.cases]:
        blind_case = package_by_case[case_id]
        blind_mapping = mapping_by_case[case_id]
        consensus = consensus_by_case[case_id]
        baseline_label = "A" if blind_mapping.A == dataset.baseline_identity.engine_id else "B"
        candidate_label = "B" if baseline_label == "A" else "A"
        baseline_ratings = getattr(consensus.ratings, baseline_label)
        candidate_ratings = getattr(consensus.ratings, candidate_label)
        target_values = aggregate.setdefault(blind_case.target, {})
        for dimension in ALL_DIMENSIONS[blind_case.target]:
            target_values.setdefault(dimension, []).append(
                (baseline_ratings[dimension], candidate_ratings[dimension])
            )
        case_results.append(
            BusinessEvalCaseResult(
                case_id=case_id,
                target=blind_case.target,
                source_input_sha256=blind_case.source_input_sha256,
                baseline_ratings=baseline_ratings,
                candidate_ratings=candidate_ratings,
                annotation_record_sha256s=tuple(
                    sorted(
                        record.annotation_record_sha256
                        for record in records_by_case[case_id]
                    )
                ),
                consensus_record_sha256=consensus.consensus_record_sha256,
            )
        )
    metrics = {
        target: {
            dimension: _summarize_metric(values)
            for dimension, values in dimensions.items()
        }
        for target, dimensions in aggregate.items()
    }
    failed_thresholds = (
        _failed_business_thresholds(metrics, registration.target_thresholds)
        if registration is not None
        else ()
    )
    payload = {
        "schema_version": "knowledge-business-eval-result-v1",
        "created_at": result_created_at,
        "dataset_version": dataset.dataset_version,
        "dataset_sha256": dataset.dataset_sha256(),
        "split": package.split,
        "package_sha256": package.package_sha256,
        "mapping_sha256": mapping.mapping_sha256,
        "registration_sha256": (
            registration.registration_sha256 if registration is not None else None
        ),
        "baseline_identity": dataset.baseline_identity,
        "candidate_identity": dataset.candidate_identity,
        "agreement_metric": annotations.governance.agreement_metric,
        "agreement_value": annotations.governance.agreement_value,
        "annotation_set_sha256": canonical_sha256(
            annotations.model_dump(mode="json")
        ),
        "metrics": metrics,
        "cases": tuple(case_results),
        "thresholds_passed": (
            not failed_thresholds if registration is not None else None
        ),
        "failed_thresholds": failed_thresholds,
    }
    return BusinessEvalResultArtifact(
        **payload,
        artifact_sha256=canonical_sha256(payload),
    )


def write_business_eval_artifact(artifact: BaseModel, path: Path | str) -> Path:
    return write_frozen_eval_artifact(artifact, path)


def load_blind_package(path: Path | str) -> BlindBusinessEvalPackage:
    return BlindBusinessEvalPackage.model_validate_json(
        Path(path).read_text(encoding="utf-8")
    )


def load_blind_mapping(path: Path | str) -> BlindBusinessEvalMapping:
    return BlindBusinessEvalMapping.model_validate_json(
        Path(path).read_text(encoding="utf-8")
    )


def load_business_annotations(path: Path | str) -> BusinessEvalAnnotationSet:
    return BusinessEvalAnnotationSet.model_validate_json(
        Path(path).read_text(encoding="utf-8")
    )


def load_business_threshold_registration(
    path: Path | str,
) -> BusinessEvalThresholdRegistration:
    return BusinessEvalThresholdRegistration.model_validate_json(
        Path(path).read_text(encoding="utf-8")
    )


def _validate_package_mapping(dataset, package, mapping) -> None:
    dataset_sha256 = dataset.dataset_sha256()
    if package.dataset_sha256 != dataset_sha256 or mapping.dataset_sha256 != dataset_sha256:
        raise ValueError("blind artifacts do not match the dataset")
    if mapping.package_sha256 != package.package_sha256:
        raise ValueError("blind mapping does not match the package")
    package_ids = tuple(case.case_id for case in package.cases)
    mapping_ids = tuple(case.case_id for case in mapping.cases)
    expected_ids = {case.case_id for case in dataset.cases if case.split == package.split}
    if set(package_ids) != expected_ids or package_ids != mapping_ids:
        raise ValueError("blind artifacts do not contain the expected ordered cases")
    expected_engines = {
        dataset.baseline_identity.engine_id,
        dataset.candidate_identity.engine_id,
    }
    if any({case.A, case.B} != expected_engines for case in mapping.cases):
        raise ValueError("blind mapping contains an unknown engine")


def _validate_annotations(dataset, package, annotations) -> None:
    if annotations.dataset_sha256 != dataset.dataset_sha256():
        raise ValueError("annotations do not match the dataset")
    if annotations.package_sha256 != package.package_sha256:
        raise ValueError("annotations do not match the blind package")
    if annotations.split != package.split:
        raise ValueError("annotation split does not match the blind package")
    if annotations.governance.minimum_annotators_per_case < (
        dataset.governance.minimum_annotators_per_case
    ):
        raise ValueError("annotation protocol weakens the dataset annotator minimum")
    if annotations.governance.protocol_version != dataset.governance.protocol_version:
        raise ValueError("annotation protocol version does not match the dataset")
    if annotations.governance.annotator_roles != dataset.governance.annotator_roles:
        raise ValueError("annotation roles do not match the frozen protocol")
    if (
        annotations.governance.minimum_qualification
        != dataset.governance.minimum_qualification
    ):
        raise ValueError("annotation qualification does not match the frozen protocol")
    if annotations.governance.adjudication_rule != dataset.governance.adjudication_rule:
        raise ValueError("annotation adjudication does not match the frozen protocol")
    if annotations.governance.agreement_metric != dataset.governance.agreement_metric:
        raise ValueError("annotation agreement metric does not match the dataset")
    if annotations.governance.minimum_agreement < dataset.governance.minimum_agreement:
        raise ValueError("annotation protocol weakens the agreement minimum")
    if annotations.governance.collection_started_at <= package.created_at:
        raise ValueError("annotation collection must begin after blind package creation")
    package_by_case = {case.case_id: case for case in package.cases}
    dataset_by_case = {case.case_id: case for case in dataset.cases}
    expected_ids = set(package_by_case)
    records_by_case: dict[str, list[BusinessEvalAnnotationRecord]] = {}
    for record in annotations.records:
        records_by_case.setdefault(record.case_id, []).append(record)
    consensus_by_case = {record.case_id: record for record in annotations.consensus}
    if set(records_by_case) != expected_ids or set(consensus_by_case) != expected_ids:
        raise ValueError("annotations require records and consensus for every case")
    if len(consensus_by_case) != len(annotations.consensus):
        raise ValueError("annotations contain duplicate consensus cases")
    for case_id, blind_case in package_by_case.items():
        source_case = dataset_by_case[case_id]
        if blind_case.source_input_sha256 != source_case.source_input_sha256():
            raise ValueError(f"case {case_id} source input hash does not match dataset")
        if (
            blind_case.target != source_case.target
            or blind_case.scenario_type != source_case.scenario_type
            or blind_case.evidence_ids != source_case.evidence_ids
            or blind_case.evidence_availability != source_case.evidence_availability
            or blind_case.evidence_sufficiency != source_case.evidence_sufficiency
            or blind_case.system_failure_scenario
            != source_case.system_failure_scenario
        ):
            raise ValueError(f"case {case_id} blind metadata does not match dataset")
        records = records_by_case[case_id]
        annotators = {record.annotator_identity_sha256 for record in records}
        minimum = annotations.governance.minimum_annotators_per_case
        if len(records) < minimum or len(annotators) != len(records):
            raise ValueError(f"case {case_id} lacks independent annotators")
        expected_dimensions = set(ALL_DIMENSIONS[blind_case.target])
        for record in (*records, consensus_by_case[case_id]):
            if set(record.ratings.A) != expected_dimensions or set(
                record.ratings.B
            ) != expected_dimensions:
                raise ValueError(f"case {case_id} has incomplete or unknown dimensions")
        record_hashes = {record.annotation_record_sha256 for record in records}
        if set(consensus_by_case[case_id].source_annotation_record_sha256s) != record_hashes:
            raise ValueError(f"case {case_id} consensus does not bind all annotations")


def _validate_registration(dataset, package, mapping, annotations, registration) -> None:
    expected = {
        "dataset_sha256": dataset.dataset_sha256(),
        "package_sha256": package.package_sha256,
        "mapping_sha256": mapping.mapping_sha256,
        "baseline_identity": dataset.baseline_identity,
        "candidate_identity": dataset.candidate_identity,
    }
    for field_name, value in expected.items():
        if getattr(registration, field_name) != value:
            raise ValueError(f"threshold registration has mismatched {field_name}")
    if registration.registered_at <= package.created_at:
        raise ValueError("threshold registration must be after blind package creation")
    if registration.registered_at >= annotations.governance.collection_started_at:
        raise ValueError("thresholds must be registered before holdout annotation begins")


def _validate_thresholds(thresholds) -> None:
    if set(thresholds) != {"followup", "reviewer"}:
        raise ValueError("threshold registration must cover followup and reviewer")
    for target, values in thresholds.items():
        positive = set(POSITIVE_DIMENSIONS[target])
        negative = set(NEGATIVE_DIMENSIONS[target])
        if set(values.minimum_deltas) != positive:
            raise ValueError(f"{target} minimum deltas must cover all positive dimensions")
        if set(values.maximum_deltas) != negative:
            raise ValueError(f"{target} maximum deltas must cover all negative dimensions")
        if values.primary_metric not in positive | negative:
            raise ValueError(f"{target} primary metric is not a known dimension")


def _summarize_metric(values: list[tuple[float, float]]) -> BusinessEvalMetricResult:
    baseline = sum(value[0] for value in values) / len(values)
    candidate = sum(value[1] for value in values) / len(values)
    return BusinessEvalMetricResult(
        baseline=baseline,
        candidate=candidate,
        delta=candidate - baseline,
        candidate_wins=sum(candidate_value > baseline_value for baseline_value, candidate_value in values),
        ties=sum(candidate_value == baseline_value for baseline_value, candidate_value in values),
        candidate_losses=sum(candidate_value < baseline_value for baseline_value, candidate_value in values),
    )


def _failed_business_thresholds(metrics, thresholds) -> tuple[str, ...]:
    failures = []
    for target, target_thresholds in thresholds.items():
        for dimension, minimum in target_thresholds.minimum_deltas.items():
            if metrics[target][dimension].delta < minimum:
                failures.append(f"{target}.{dimension}.minimum_delta")
        for dimension, maximum in target_thresholds.maximum_deltas.items():
            if metrics[target][dimension].delta > maximum:
                failures.append(f"{target}.{dimension}.maximum_delta")
    return tuple(sorted(failures))


def _validate_timestamp(value: datetime, field_name: str) -> None:
    if value.tzinfo is None:
        raise ValueError(f"{field_name} must be timezone-aware")


def _validate_unique_case_ids(cases, artifact_name: str) -> None:
    case_ids = [case.case_id for case in cases]
    if len(case_ids) != len(set(case_ids)):
        raise ValueError(f"{artifact_name} case IDs must be unique")


def _validate_self_hash(model: BaseModel, field_name: str, artifact_name: str) -> None:
    payload = model.model_dump(mode="json", exclude={field_name})
    if canonical_sha256(payload) != getattr(model, field_name):
        raise ValueError(f"{artifact_name} SHA-256 mismatch")
