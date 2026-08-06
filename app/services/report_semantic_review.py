from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


SEMANTIC_REVIEW_PROTOCOL_VERSION = "report-semantic-blind-review-v1"
SEMANTIC_REVIEW_HASH_ALGORITHM = "sha256-canonical-json-v1"

VariantVersion = Literal["v1", "v2"]
BlindLabel = Literal["A", "B"]
FabricationStatus = Literal["not_observed", "observed", "uncertain"]
Preference = Literal["A", "B", "tie"]


def _canonical_json(value: Any) -> bytes:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def text_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class SemanticReviewPresentation(BaseModel):
    """Candidate-visible report content with all version metadata removed."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    conclusion: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    actions: list[str] = Field(min_length=1, max_length=3)
    per_question_feedback: list[str] = Field(min_length=1)
    limitations: list[str] = Field(min_length=1)


class SemanticReviewSourcePair(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    pair_id: str = Field(min_length=1)
    case_id: str = Field(min_length=1)
    candidate_answer: str = Field(min_length=1)
    candidate_answer_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    coverage_types: list[str] = Field(min_length=1)
    critical_fabrication_case: bool
    source_classification: Literal["synthetic"] = "synthetic"
    contains_real_candidate_data: Literal[False] = False
    contains_principal_memory: Literal[False] = False
    v1: SemanticReviewPresentation
    v2: SemanticReviewPresentation

    @model_validator(mode="after")
    def validate_candidate_hash(self):
        if text_sha256(self.candidate_answer) != self.candidate_answer_sha256:
            raise ValueError("candidate_answer_sha256 does not match candidate_answer")
        if self.v1 == self.v2:
            raise ValueError("v1 and v2 presentations must differ")
        return self


class SemanticReviewDataset(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["report-semantic-review-pairs-v1"]
    dataset_id: str = Field(min_length=1)
    dataset_version: str = Field(min_length=1)
    limitations: str = Field(min_length=1)
    pairs: list[SemanticReviewSourcePair] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_pairs(self):
        pair_ids = [pair.pair_id for pair in self.pairs]
        case_ids = [pair.case_id for pair in self.pairs]
        if len(pair_ids) != len(set(pair_ids)):
            raise ValueError("duplicate semantic review pair_id")
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("duplicate semantic review case_id")
        if not any(pair.critical_fabrication_case for pair in self.pairs):
            raise ValueError("dataset needs at least one critical fabrication case")
        return self


class BlindedReviewVariant(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    label: BlindLabel
    presentation: SemanticReviewPresentation
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_content_hash(self):
        if canonical_sha256(self.presentation) != self.content_sha256:
            raise ValueError("blinded variant content_sha256 mismatch")
        return self


class BlindedReviewPair(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    pair_id: str = Field(min_length=1)
    case_id: str = Field(min_length=1)
    candidate_answer: str = Field(min_length=1)
    candidate_answer_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    coverage_types: list[str] = Field(min_length=1)
    critical_fabrication_case: bool
    variant_a: BlindedReviewVariant
    variant_b: BlindedReviewVariant

    @model_validator(mode="after")
    def validate_pair(self):
        if self.variant_a.label != "A" or self.variant_b.label != "B":
            raise ValueError("blinded variants must use A/B labels")
        if text_sha256(self.candidate_answer) != self.candidate_answer_sha256:
            raise ValueError("blinded candidate answer hash mismatch")
        if self.variant_a.content_sha256 == self.variant_b.content_sha256:
            raise ValueError("blinded variants must differ")
        return self


class BlindedReviewPacket(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    protocol_version: Literal["report-semantic-blind-review-v1"]
    dataset_id: str = Field(min_length=1)
    dataset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    seed_commitment_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    assignment_commitment_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    hash_algorithm: Literal["sha256-canonical-json-v1"]
    reviewer_instructions: list[str] = Field(min_length=1)
    pairs: list[BlindedReviewPair] = Field(min_length=1)


class ReviewAssignment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    pair_id: str = Field(min_length=1)
    variant_a_version: VariantVersion
    variant_b_version: VariantVersion

    @model_validator(mode="after")
    def validate_assignment(self):
        if self.variant_a_version == self.variant_b_version:
            raise ValueError("A and B must map to different source versions")
        return self


class ReviewAssignmentKey(BaseModel):
    """Coordinator-only artifact. It must never be given to a reviewer."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    protocol_version: Literal["report-semantic-blind-review-v1"]
    dataset_id: str = Field(min_length=1)
    dataset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    randomization_seed: str = Field(min_length=16)
    seed_commitment_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    assignment_commitment_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    packet_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    assignments: list[ReviewAssignment] = Field(min_length=1)


class BlindedReviewArtifacts(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    packet: BlindedReviewPacket
    assignment_key: ReviewAssignmentKey


REVIEWER_INSTRUCTIONS = [
    "Review A and B without attempting to infer their source version.",
    "Judge technical correctness and whether every evaluation is supported by the candidate answer.",
    "Flag invented candidate company, scale, responsibility, metric, money, latency, action, or result.",
    "Judge cross-question coverage, actionability, and tone calibration independently for A and B.",
    "Do not treat an optional automated Judge as a substitute for human forbidden-item review.",
]


def load_semantic_review_dataset(path: Path | str) -> SemanticReviewDataset:
    return SemanticReviewDataset.model_validate_json(
        Path(path).read_text(encoding="utf-8")
    )


def semantic_review_dataset_sha256(path: Path | str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def build_blinded_review_artifacts(
    dataset: SemanticReviewDataset,
    *,
    randomization_seed: str,
) -> BlindedReviewArtifacts:
    if len(randomization_seed) < 16:
        raise ValueError("randomization_seed must contain at least 16 characters")
    dataset_sha256 = canonical_sha256(dataset)
    assignments: list[ReviewAssignment] = []
    blinded_pairs: list[BlindedReviewPair] = []

    for pair in dataset.pairs:
        bit = hashlib.sha256(
            f"{randomization_seed}\0{pair.pair_id}".encode("utf-8")
        ).digest()[0] & 1
        a_version: VariantVersion = "v1" if bit == 0 else "v2"
        b_version: VariantVersion = "v2" if bit == 0 else "v1"
        assignment = ReviewAssignment(
            pair_id=pair.pair_id,
            variant_a_version=a_version,
            variant_b_version=b_version,
        )
        assignments.append(assignment)
        a_presentation = getattr(pair, a_version)
        b_presentation = getattr(pair, b_version)
        blinded_pairs.append(
            BlindedReviewPair(
                pair_id=pair.pair_id,
                case_id=pair.case_id,
                candidate_answer=pair.candidate_answer,
                candidate_answer_sha256=pair.candidate_answer_sha256,
                coverage_types=pair.coverage_types,
                critical_fabrication_case=pair.critical_fabrication_case,
                variant_a=BlindedReviewVariant(
                    label="A",
                    presentation=a_presentation,
                    content_sha256=canonical_sha256(a_presentation),
                ),
                variant_b=BlindedReviewVariant(
                    label="B",
                    presentation=b_presentation,
                    content_sha256=canonical_sha256(b_presentation),
                ),
            )
        )

    assignment_payload = [item.model_dump(mode="json") for item in assignments]
    assignment_commitment = canonical_sha256(
        {
            "dataset_sha256": dataset_sha256,
            "randomization_seed": randomization_seed,
            "assignments": assignment_payload,
        }
    )
    seed_commitment = text_sha256(randomization_seed)
    packet = BlindedReviewPacket(
        protocol_version=SEMANTIC_REVIEW_PROTOCOL_VERSION,
        dataset_id=dataset.dataset_id,
        dataset_sha256=dataset_sha256,
        seed_commitment_sha256=seed_commitment,
        assignment_commitment_sha256=assignment_commitment,
        hash_algorithm=SEMANTIC_REVIEW_HASH_ALGORITHM,
        reviewer_instructions=REVIEWER_INSTRUCTIONS,
        pairs=blinded_pairs,
    )
    key = ReviewAssignmentKey(
        protocol_version=SEMANTIC_REVIEW_PROTOCOL_VERSION,
        dataset_id=dataset.dataset_id,
        dataset_sha256=dataset_sha256,
        randomization_seed=randomization_seed,
        seed_commitment_sha256=seed_commitment,
        assignment_commitment_sha256=assignment_commitment,
        packet_sha256=canonical_sha256(packet),
        assignments=assignments,
    )
    return BlindedReviewArtifacts(packet=packet, assignment_key=key)


class VariantScores(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    A: int = Field(ge=1, le=5)
    B: int = Field(ge=1, le=5)


class FabricationAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: FabricationStatus
    rationale: str = Field(min_length=1)
    evidence_fragments: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_evidence(self):
        if self.status == "observed" and not self.evidence_fragments:
            raise ValueError("observed fabrication requires evidence fragments")
        return self


class HumanSemanticJudgment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    pair_id: str = Field(min_length=1)
    reviewer_id: str = Field(min_length=1)
    reviewer_role: Literal["independent_technical_reviewer"]
    independence_attested: Literal[True]
    assignment_was_hidden: Literal[True]
    technical_correctness: VariantScores
    answer_support: VariantScores
    experience_fabrication_A: FabricationAssessment
    experience_fabrication_B: FabricationAssessment
    summary_coverage: VariantScores
    actionability: VariantScores
    tone_calibration: VariantScores
    preferred_variant: Preference
    preference_rationale: str = Field(min_length=1)
    critical_forbidden_item_checked: bool
    false_positive: bool
    false_negative: bool
    error_notes: str | None = None

    @model_validator(mode="after")
    def validate_error_notes(self):
        if (self.false_positive or self.false_negative) and not self.error_notes:
            raise ValueError("false positive/negative records require error_notes")
        return self


class HumanReviewSheet(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    protocol_version: Literal["report-semantic-blind-review-v1"]
    packet_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    judgments: list[HumanSemanticJudgment] = Field(default_factory=list)


class OfflineJudgeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = False
    execution_scope: Literal["offline_only"] = "offline_only"
    online_runtime_prohibited: Literal[True] = True
    provider: str | None = None
    model: str | None = None
    prompt_version: str | None = None
    prompt_text: str | None = None
    prompt_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    dataset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_frozen_config(self):
        frozen = (
            self.provider,
            self.model,
            self.prompt_version,
            self.prompt_text,
            self.prompt_sha256,
        )
        if self.enabled and not all(frozen):
            raise ValueError(
                "enabled offline Judge requires frozen provider, model, prompt, version, and hash"
            )
        if self.prompt_text is not None:
            if self.prompt_sha256 != text_sha256(self.prompt_text):
                raise ValueError("offline Judge prompt_sha256 mismatch")
        elif self.prompt_sha256 is not None:
            raise ValueError("offline Judge prompt hash requires prompt text")
        return self


class OfflineJudgeFinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    pair_id: str = Field(min_length=1)
    fabrication_A: FabricationStatus
    fabrication_B: FabricationStatus
    preferred_variant: Preference
    rationale: str = Field(min_length=1)


class OfflineJudgeBundle(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    config_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    packet_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider_calls: int = Field(ge=0)
    findings: list[OfflineJudgeFinding]


class SemanticReviewGateResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    protocol_version: str
    quality_status: Literal[
        "PASS",
        "BLOCKED_INDEPENDENT_HUMAN_REVIEW_NOT_RUN",
        "BLOCKED_INCOMPLETE_HUMAN_REVIEW",
        "BLOCKED_CRITICAL_DOUBLE_REVIEW_NOT_RUN",
        "BLOCKED_CRITICAL_FORBIDDEN_ITEM_UNRESOLVED",
        "FAIL_CANDIDATE_EXPERIENCE_FABRICATION",
        "FAIL_SEMANTIC_THRESHOLDS",
        "FAIL_PROTOCOL_INTEGRITY",
    ]
    human_review_status: Literal["NOT_RUN", "INCOMPLETE", "COMPLETE"]
    sample_size: int = Field(ge=0)
    critical_case_count: int = Field(ge=0)
    completed_judgment_count: int = Field(ge=0)
    independent_reviewer_count: int = Field(ge=0)
    missing_primary_review_pair_ids: list[str]
    missing_second_review_pair_ids: list[str]
    v2_fabrication_observed_count: int | None = Field(default=None, ge=0)
    v2_fabrication_uncertain_count: int | None = Field(default=None, ge=0)
    false_positive_count: int | None = Field(default=None, ge=0)
    false_negative_count: int | None = Field(default=None, ge=0)
    v2_preferred_count: int | None = Field(default=None, ge=0)
    v1_preferred_count: int | None = Field(default=None, ge=0)
    tie_count: int | None = Field(default=None, ge=0)
    v2_technical_correctness_pass_rate: float | None = Field(
        default=None, ge=0, le=1
    )
    v2_answer_support_pass_rate: float | None = Field(default=None, ge=0, le=1)
    v2_summary_coverage_pass_rate: float | None = Field(default=None, ge=0, le=1)
    v2_actionability_pass_rate: float | None = Field(default=None, ge=0, le=1)
    v2_tone_calibration_pass_rate: float | None = Field(default=None, ge=0, le=1)
    v2_helpfulness_noninferiority_rate: float | None = Field(
        default=None, ge=0, le=1
    )
    provider_calls: int = Field(ge=0)
    offline_judge_used: bool
    issue_codes: list[str]


def empty_human_review_sheet(packet: BlindedReviewPacket) -> HumanReviewSheet:
    return HumanReviewSheet(
        protocol_version=SEMANTIC_REVIEW_PROTOCOL_VERSION,
        packet_sha256=canonical_sha256(packet),
        judgments=[],
    )


def disabled_offline_judge_config(
    packet: BlindedReviewPacket,
) -> OfflineJudgeConfig:
    return OfflineJudgeConfig(
        enabled=False,
        dataset_sha256=packet.dataset_sha256,
    )


def evaluate_semantic_review_gate(
    *,
    source_dataset: SemanticReviewDataset,
    packet: BlindedReviewPacket,
    assignment_key: ReviewAssignmentKey,
    review_sheet: HumanReviewSheet,
    judge_config: OfflineJudgeConfig,
    judge_bundle: OfflineJudgeBundle | None = None,
    execution_context: Literal["offline", "online"] = "offline",
) -> SemanticReviewGateResult:
    issues = _integrity_issues(
        source_dataset=source_dataset,
        packet=packet,
        assignment_key=assignment_key,
        review_sheet=review_sheet,
        judge_config=judge_config,
        judge_bundle=judge_bundle,
        execution_context=execution_context,
    )
    pair_by_id = {pair.pair_id: pair for pair in packet.pairs}
    assignment_by_id = {
        assignment.pair_id: assignment for assignment in assignment_key.assignments
    }
    judgments_by_pair: dict[str, list[HumanSemanticJudgment]] = defaultdict(list)
    known_judgments: list[HumanSemanticJudgment] = []
    for judgment in review_sheet.judgments:
        if judgment.pair_id not in pair_by_id:
            issues.append("UNKNOWN_REVIEW_PAIR")
            continue
        judgments_by_pair[judgment.pair_id].append(judgment)
        known_judgments.append(judgment)

    for pair_id, judgments in judgments_by_pair.items():
        reviewer_ids = [judgment.reviewer_id for judgment in judgments]
        if len(reviewer_ids) != len(set(reviewer_ids)):
            issues.append("DUPLICATE_REVIEWER_PAIR_JUDGMENT")

    missing_primary: list[str] = []
    missing_second: list[str] = []
    for pair in packet.pairs:
        reviewer_ids = {
            judgment.reviewer_id for judgment in judgments_by_pair[pair.pair_id]
        }
        if not reviewer_ids:
            missing_primary.append(pair.pair_id)
        if pair.critical_fabrication_case and len(reviewer_ids) < 2:
            missing_second.append(pair.pair_id)
        if pair.critical_fabrication_case and any(
            not judgment.critical_forbidden_item_checked
            for judgment in judgments_by_pair[pair.pair_id]
        ):
            issues.append("CRITICAL_FORBIDDEN_ITEM_NOT_CHECKED")

    observed = 0
    uncertain = 0
    v2_preferred = 0
    v1_preferred = 0
    ties = 0
    v2_scores: dict[str, list[int]] = {
        "technical_correctness": [],
        "answer_support": [],
        "summary_coverage": [],
        "actionability": [],
        "tone_calibration": [],
    }
    for judgment in known_judgments:
        assignment = assignment_by_id.get(judgment.pair_id)
        if assignment is None:
            continue
        v2_label: BlindLabel = (
            "A" if assignment.variant_a_version == "v2" else "B"
        )
        assessment = (
            judgment.experience_fabrication_A
            if v2_label == "A"
            else judgment.experience_fabrication_B
        )
        if assessment.status == "observed":
            observed += 1
        elif assessment.status == "uncertain":
            uncertain += 1
        if judgment.preferred_variant == "tie":
            ties += 1
        elif judgment.preferred_variant == v2_label:
            v2_preferred += 1
        else:
            v1_preferred += 1
        for dimension in v2_scores:
            scores = getattr(judgment, dimension)
            v2_scores[dimension].append(getattr(scores, v2_label))

    pass_rates = {
        dimension: (
            sum(score >= 4 for score in scores) / len(scores)
            if scores
            else None
        )
        for dimension, scores in v2_scores.items()
    }
    semantic_threshold_failed = any(
        pass_rates[dimension] is not None
        and pass_rates[dimension] < 0.90
        for dimension in (
            "technical_correctness",
            "answer_support",
            "summary_coverage",
            "actionability",
        )
    )

    if issues:
        quality_status = "FAIL_PROTOCOL_INTEGRITY"
        human_status = "NOT_RUN" if not known_judgments else "INCOMPLETE"
    elif not known_judgments:
        quality_status = "BLOCKED_INDEPENDENT_HUMAN_REVIEW_NOT_RUN"
        human_status = "NOT_RUN"
    elif missing_primary:
        quality_status = "BLOCKED_INCOMPLETE_HUMAN_REVIEW"
        human_status = "INCOMPLETE"
    elif missing_second:
        quality_status = "BLOCKED_CRITICAL_DOUBLE_REVIEW_NOT_RUN"
        human_status = "INCOMPLETE"
    elif observed:
        quality_status = "FAIL_CANDIDATE_EXPERIENCE_FABRICATION"
        human_status = "COMPLETE"
    elif uncertain:
        quality_status = "BLOCKED_CRITICAL_FORBIDDEN_ITEM_UNRESOLVED"
        human_status = "COMPLETE"
    elif semantic_threshold_failed:
        quality_status = "FAIL_SEMANTIC_THRESHOLDS"
        human_status = "COMPLETE"
    else:
        quality_status = "PASS"
        human_status = "COMPLETE"

    return SemanticReviewGateResult(
        protocol_version=SEMANTIC_REVIEW_PROTOCOL_VERSION,
        quality_status=quality_status,
        human_review_status=human_status,
        sample_size=len(packet.pairs),
        critical_case_count=sum(
            pair.critical_fabrication_case for pair in packet.pairs
        ),
        completed_judgment_count=len(known_judgments),
        independent_reviewer_count=len(
            {judgment.reviewer_id for judgment in known_judgments}
        ),
        missing_primary_review_pair_ids=missing_primary,
        missing_second_review_pair_ids=missing_second,
        v2_fabrication_observed_count=(observed if known_judgments else None),
        v2_fabrication_uncertain_count=(uncertain if known_judgments else None),
        false_positive_count=(
            sum(judgment.false_positive for judgment in known_judgments)
            if known_judgments
            else None
        ),
        false_negative_count=(
            sum(judgment.false_negative for judgment in known_judgments)
            if known_judgments
            else None
        ),
        v2_preferred_count=(v2_preferred if known_judgments else None),
        v1_preferred_count=(v1_preferred if known_judgments else None),
        tie_count=(ties if known_judgments else None),
        v2_technical_correctness_pass_rate=pass_rates["technical_correctness"],
        v2_answer_support_pass_rate=pass_rates["answer_support"],
        v2_summary_coverage_pass_rate=pass_rates["summary_coverage"],
        v2_actionability_pass_rate=pass_rates["actionability"],
        v2_tone_calibration_pass_rate=pass_rates["tone_calibration"],
        v2_helpfulness_noninferiority_rate=(
            (v2_preferred + ties) / len(known_judgments)
            if known_judgments
            else None
        ),
        provider_calls=judge_bundle.provider_calls if judge_bundle else 0,
        offline_judge_used=judge_bundle is not None,
        issue_codes=sorted(set(issues)),
    )


def _integrity_issues(
    *,
    source_dataset: SemanticReviewDataset,
    packet: BlindedReviewPacket,
    assignment_key: ReviewAssignmentKey,
    review_sheet: HumanReviewSheet,
    judge_config: OfflineJudgeConfig,
    judge_bundle: OfflineJudgeBundle | None,
    execution_context: Literal["offline", "online"],
) -> list[str]:
    issues: list[str] = []
    packet_sha256 = canonical_sha256(packet)
    if execution_context == "online" and (judge_config.enabled or judge_bundle):
        issues.append("ONLINE_SEMANTIC_JUDGE_PROHIBITED")
    if assignment_key.dataset_id != packet.dataset_id:
        issues.append("ASSIGNMENT_DATASET_ID_MISMATCH")
    if source_dataset.dataset_id != packet.dataset_id:
        issues.append("SOURCE_DATASET_ID_MISMATCH")
    if canonical_sha256(source_dataset) != packet.dataset_sha256:
        issues.append("SOURCE_DATASET_HASH_MISMATCH")
    if assignment_key.dataset_sha256 != packet.dataset_sha256:
        issues.append("ASSIGNMENT_DATASET_HASH_MISMATCH")
    if assignment_key.packet_sha256 != packet_sha256:
        issues.append("ASSIGNMENT_PACKET_HASH_MISMATCH")
    if review_sheet.packet_sha256 != packet_sha256:
        issues.append("REVIEW_SHEET_PACKET_HASH_MISMATCH")
    if text_sha256(assignment_key.randomization_seed) != packet.seed_commitment_sha256:
        issues.append("RANDOMIZATION_SEED_COMMITMENT_MISMATCH")
    if assignment_key.seed_commitment_sha256 != packet.seed_commitment_sha256:
        issues.append("KEY_SEED_COMMITMENT_MISMATCH")
    commitment = canonical_sha256(
        {
            "dataset_sha256": assignment_key.dataset_sha256,
            "randomization_seed": assignment_key.randomization_seed,
            "assignments": [
                item.model_dump(mode="json")
                for item in assignment_key.assignments
            ],
        }
    )
    if commitment != packet.assignment_commitment_sha256:
        issues.append("ASSIGNMENT_COMMITMENT_MISMATCH")
    if assignment_key.assignment_commitment_sha256 != packet.assignment_commitment_sha256:
        issues.append("KEY_ASSIGNMENT_COMMITMENT_MISMATCH")
    packet_pair_ids = {pair.pair_id for pair in packet.pairs}
    assignment_pair_ids = {
        assignment.pair_id for assignment in assignment_key.assignments
    }
    if packet_pair_ids != assignment_pair_ids:
        issues.append("ASSIGNMENT_PAIR_SET_MISMATCH")
    source_pair_ids = {pair.pair_id for pair in source_dataset.pairs}
    if packet_pair_ids != source_pair_ids:
        issues.append("SOURCE_PAIR_SET_MISMATCH")
    source_by_id = {pair.pair_id: pair for pair in source_dataset.pairs}
    packet_by_id = {pair.pair_id: pair for pair in packet.pairs}
    assignment_by_id = {
        assignment.pair_id: assignment for assignment in assignment_key.assignments
    }
    for pair_id in packet_pair_ids & source_pair_ids & assignment_pair_ids:
        source_pair = source_by_id[pair_id]
        blinded_pair = packet_by_id[pair_id]
        assignment = assignment_by_id[pair_id]
        if (
            blinded_pair.case_id != source_pair.case_id
            or blinded_pair.candidate_answer != source_pair.candidate_answer
            or blinded_pair.candidate_answer_sha256
            != source_pair.candidate_answer_sha256
            or blinded_pair.coverage_types != source_pair.coverage_types
            or blinded_pair.critical_fabrication_case
            != source_pair.critical_fabrication_case
        ):
            issues.append("SOURCE_PAIR_METADATA_MISMATCH")
        expected_a = getattr(source_pair, assignment.variant_a_version)
        expected_b = getattr(source_pair, assignment.variant_b_version)
        if (
            blinded_pair.variant_a.presentation != expected_a
            or blinded_pair.variant_a.content_sha256 != canonical_sha256(expected_a)
            or blinded_pair.variant_b.presentation != expected_b
            or blinded_pair.variant_b.content_sha256 != canonical_sha256(expected_b)
        ):
            issues.append("BLINDED_VARIANT_SOURCE_MISMATCH")
    if judge_config.dataset_sha256 != packet.dataset_sha256:
        issues.append("JUDGE_DATASET_HASH_MISMATCH")
    if judge_bundle is not None:
        if not judge_config.enabled:
            issues.append("JUDGE_BUNDLE_WITH_DISABLED_CONFIG")
        if judge_bundle.config_sha256 != canonical_sha256(judge_config):
            issues.append("JUDGE_CONFIG_HASH_MISMATCH")
        if judge_bundle.packet_sha256 != packet_sha256:
            issues.append("JUDGE_PACKET_HASH_MISMATCH")
        finding_ids = {finding.pair_id for finding in judge_bundle.findings}
        if not finding_ids.issubset(packet_pair_ids):
            issues.append("JUDGE_UNKNOWN_PAIR")
    return issues
