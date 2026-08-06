from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


DatasetId = Literal[
    "initial-question-quality-v1",
    "initial-question-quality-v2",
    "followup-decision-quality-v1",
    "followup-decision-quality-v2",
    "report-score-quality-v2",
    "report-semantic-quality-v1",
]

DATASET_CASE_TYPES = {
    "initial-question-quality-v1": "initial_question",
    "initial-question-quality-v2": "initial_question",
    "followup-decision-quality-v1": "followup_decision",
    "followup-decision-quality-v2": "followup_decision",
    "report-score-quality-v2": "report_score",
    "report-semantic-quality-v1": "report_semantic",
}


class InitialQuestionCaseInput(BaseModel):
    """Frozen, Provider-safe input contract for T57 plan-generation cases."""

    model_config = ConfigDict(extra="forbid")

    scenario_domain: Literal[
        "backend",
        "frontend",
        "data",
        "platform",
        "general_project",
        "system_design",
    ]
    job_description: str = Field(min_length=40)
    resume_summary: str = Field(min_length=40)
    configuration: dict[str, Any]
    runs_per_case: int = Field(ge=2, le=5)
    role_keywords: list[str] = Field(min_length=2)
    focus_evidence: list[str] = Field(min_length=1)
    forbidden_leak_markers: list[str] = Field(min_length=1)
    knowledge_context: list[dict[str, Any]] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_lists(self):
        for name in (
            "role_keywords",
            "focus_evidence",
            "forbidden_leak_markers",
        ):
            values = getattr(self, name)
            normalized = [value.strip() for value in values if value.strip()]
            if len(normalized) != len(values) or len(set(normalized)) != len(values):
                raise ValueError(f"{name} must contain unique non-blank values")
        return self


class SourceBoundary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    classification: Literal["synthetic", "public", "redacted"]
    description: str = Field(min_length=1)
    contains_real_candidate_data: Literal[False]
    contains_employer_confidential_data: Literal[False]
    contains_principal_memory: Literal[False]


class CaseExpectation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: str | None = None
    score_range: tuple[int, int] | None = None
    acceptable_actions: list[str] = Field(default_factory=list)
    acceptable_gaps: list[dict[str, Any]] = Field(default_factory=list)
    forbidden_gaps: list[str] = Field(default_factory=list)
    forbidden_questions: list[str] = Field(default_factory=list)
    allow_multiple_reasonable_decisions: bool = False
    expected_reason_codes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_expectation(self):
        if (self.action is None) == (self.score_range is None):
            raise ValueError("exactly one of action or score_range is required")
        if self.action is not None and not self.action.strip():
            raise ValueError("action must not be blank")
        if self.score_range is not None:
            low, high = self.score_range
            if not 0 <= low <= high <= 100:
                raise ValueError("score_range must be ordered within 0..100")
        return self


class AnnotationRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    annotator_id: str = Field(min_length=1)
    reviewer_id: str | None
    review_status: Literal["pending", "reviewed"]
    dispute_status: Literal["none", "open", "resolved"]
    resolution: str | None
    rationale: str | None = None
    review_notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_annotation(self):
        if self.review_status == "reviewed" and not self.reviewer_id:
            raise ValueError("reviewed cases require reviewer_id")
        if self.dispute_status == "resolved" and not self.resolution:
            raise ValueError("resolved disputes require a resolution")
        if self.dispute_status != "resolved" and self.resolution is not None:
            raise ValueError("resolution is only valid for resolved disputes")
        return self


class CaseHashes(BaseModel):
    model_config = ConfigDict(extra="forbid")

    algorithm: Literal["sha256-canonical-json-v1"]
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class InterviewQualityCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(min_length=1)
    case_version: int = Field(ge=1)
    language: Literal["zh-Hans", "en", "mixed"]
    case_type: Literal[
        "initial_question", "followup_decision", "report_score", "report_semantic"
    ]
    question_type: Literal[
        "technical", "system_design", "project", "behavioral", "mixed"
    ]
    difficulty: Literal["foundation", "intermediate", "advanced"]
    quality_label: Literal[
        "strong",
        "medium",
        "partial",
        "incorrect",
        "off_topic",
        "empty",
        "not_applicable",
    ]
    partition: Literal["train", "dev", "blind-test"]
    source_boundary: SourceBoundary
    input: dict[str, Any]
    expectation: CaseExpectation
    must_have_evidence: list[str]
    forbidden_inference: list[str]
    annotation: AnnotationRecord
    provider_allowed: bool
    gate_eligible: bool
    hashes: CaseHashes

    @model_validator(mode="after")
    def validate_case(self):
        if not self.input:
            raise ValueError("input must not be empty")
        if self.annotation.review_status != "reviewed" and self.gate_eligible:
            raise ValueError("unreviewed cases cannot be gate eligible")
        if self.annotation.dispute_status == "open" and self.gate_eligible:
            raise ValueError("open disputes cannot be gate eligible")
        return self


class InterviewQualityDataset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["interview-quality-dataset-contract-v1"]
    dataset_id: DatasetId
    dataset_version: str = Field(min_length=1)
    description: str = Field(min_length=1)
    fixture_only: bool
    cases: list[InterviewQualityCase] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_dataset(self):
        if self.dataset_version != self.dataset_id:
            raise ValueError("dataset_version must equal the frozen dataset_id")
        case_ids = [case.case_id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("case_id must be unique within a dataset")
        expected_type = DATASET_CASE_TYPES[self.dataset_id]
        if any(case.case_type != expected_type for case in self.cases):
            raise ValueError(f"all cases in {self.dataset_id} must use {expected_type}")
        if self.dataset_id == "report-score-quality-v2":
            if any(case.expectation.score_range is None for case in self.cases):
                raise ValueError("report-score cases require score_range")
        elif any(case.expectation.action is None for case in self.cases):
            raise ValueError(f"{self.dataset_id} cases require expected action")
        if self.dataset_id == "followup-decision-quality-v2":
            self._validate_followup_v2()
        if self.dataset_id == "initial-question-quality-v2":
            self._validate_initial_question_v2()
        if self.fixture_only and any(case.gate_eligible for case in self.cases):
            raise ValueError("contract-only fixtures cannot be gate eligible")
        return self

    def _validate_initial_question_v2(self) -> None:
        from app.services.interview_plan_revision import PlanConfigurationSnapshot

        if len(self.cases) < 12:
            raise ValueError("initial-question v2 requires at least 12 cases")
        if self.fixture_only:
            raise ValueError("initial-question v2 is an evaluation dataset, not a fixture")
        domains: set[str] = set()
        difficulties: set[str] = set()
        focuses: set[str] = set()
        durations: set[int] = set()
        partitions: set[str] = set()
        languages: set[str] = set()
        for case in self.cases:
            item = InitialQuestionCaseInput.model_validate(case.input)
            configuration = PlanConfigurationSnapshot.model_validate(
                item.configuration
            )
            if configuration.difficulty != case.difficulty:
                raise ValueError(
                    "initial-question case difficulty must match configuration"
                )
            if case.question_type != "mixed":
                raise ValueError("initial-question plan cases must use question_type=mixed")
            if case.quality_label != "strong":
                raise ValueError("initial-question source cases must be strong inputs")
            if not case.provider_allowed:
                raise ValueError("initial-question cases must be Provider-eligible")
            domains.add(item.scenario_domain)
            difficulties.add(configuration.difficulty)
            focuses.add(configuration.focus_preset)
            durations.add(configuration.target_duration_minutes)
            partitions.add(case.partition)
            languages.add(case.language)
        if domains != {
            "backend",
            "frontend",
            "data",
            "platform",
            "general_project",
            "system_design",
        }:
            raise ValueError("initial-question domain coverage is incomplete")
        if difficulties != {"foundation", "intermediate", "advanced"}:
            raise ValueError("initial-question difficulty coverage is incomplete")
        if focuses != {
            "technical_depth",
            "system_design",
            "project_review",
            "balanced",
        }:
            raise ValueError("initial-question focus coverage is incomplete")
        if durations != {15, 30, 45, 60}:
            raise ValueError("initial-question duration coverage is incomplete")
        if partitions != {"train", "dev", "blind-test"}:
            raise ValueError("initial-question partition coverage is incomplete")
        if "zh-Hans" not in languages or "en" not in languages:
            raise ValueError("initial-question dataset requires Chinese and English")
        if sum(case.language == "zh-Hans" for case in self.cases) <= len(self.cases) / 2:
            raise ValueError("initial-question dataset must be Chinese-majority")

    def _validate_followup_v2(self) -> None:
        if not 80 <= len(self.cases) <= 120:
            raise ValueError("followup v2 requires 80..120 cases")
        sequence_steps: dict[str, set[int]] = {}
        sequence_partitions: dict[str, set[str]] = {}
        partition_coverage: dict[str, set[str]] = {
            "train": set(),
            "dev": set(),
            "blind-test": set(),
        }
        adversarial_count = 0
        coverage: set[str] = set()
        languages: set[str] = set()
        knowledge_boundaries: set[str] = set()
        memory_modes: set[str] = set()
        for case in self.cases:
            languages.add(case.language)
            tags = set(case.input.get("scenario_tags") or [])
            coverage.update(tags)
            partition_coverage[case.partition].update(tags)
            if "adversarial" in tags:
                adversarial_count += 1
            knowledge_boundaries.add(str(case.input.get("knowledge_boundary")))
            memory_modes.add(str(case.input.get("memory_mode")))
            sequence_id = case.input.get("sequence_id")
            sequence_step = case.input.get("sequence_step")
            if sequence_id is not None:
                if sequence_step not in {1, 2}:
                    raise ValueError("followup sequence_step must be 1 or 2")
                sequence_steps.setdefault(str(sequence_id), set()).add(
                    int(sequence_step)
                )
                sequence_partitions.setdefault(str(sequence_id), set()).add(
                    case.partition
                )
            expectation = case.expectation
            if expectation.action not in {"follow_up", "next_question"}:
                raise ValueError("followup action must be bounded")
            if not expectation.acceptable_actions:
                raise ValueError("followup cases require acceptable_actions")
            if expectation.action not in expectation.acceptable_actions:
                raise ValueError("expected action must be acceptable")
            if not expectation.expected_reason_codes:
                raise ValueError("followup cases require expected reason codes")
            if not expectation.forbidden_questions:
                raise ValueError("followup cases require forbidden questions")
            if expectation.action == "follow_up" and not expectation.acceptable_gaps:
                raise ValueError("follow_up cases require acceptable gaps")
            if not (case.annotation.rationale or "").strip():
                raise ValueError("followup cases require annotation rationale")
            if case.gate_eligible:
                raise ValueError("followup v2 remains ineligible before review")
        if len(sequence_steps) < 20 or any(
            steps != {1, 2} for steps in sequence_steps.values()
        ):
            raise ValueError("followup v2 requires at least 20 complete two-step sequences")
        if any(len(partitions) != 1 for partitions in sequence_partitions.values()):
            raise ValueError("a followup sequence cannot cross partitions")
        if adversarial_count < 20:
            raise ValueError("followup v2 requires at least 20 adversarial cases")
        required_coverage = {
            "strong_answer",
            "single_critical_gap",
            "technical_error",
            "off_topic",
            "empty_answer",
            "duplicate_gap",
            "repeated_question",
            "provider_timeout",
            "provider_invalid_output",
            "provider_failed",
            "low_confidence",
            "prompt_injection",
            "mixed_language",
        }
        if not required_coverage <= coverage:
            raise ValueError("followup v2 coverage matrix is incomplete")
        if languages != {"zh-Hans", "en", "mixed"}:
            raise ValueError("followup v2 requires zh-Hans, en and mixed cases")
        if not {"none", "public_evidence", "local_auxiliary"} <= knowledge_boundaries:
            raise ValueError("followup v2 knowledge boundaries are incomplete")
        if not {"disabled", "local_auxiliary"} <= memory_modes:
            raise ValueError("followup v2 memory boundaries are incomplete")
        for partition, tags in partition_coverage.items():
            if not {
                "strong_answer",
                "single_critical_gap",
                "adversarial",
            } <= tags:
                raise ValueError(
                    f"followup v2 partition coverage is incomplete: {partition}"
                )


def canonical_json_bytes(payload: Any) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def sha256_canonical_json(payload: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def expected_case_hashes(case: InterviewQualityCase) -> tuple[str, str]:
    # Schema extensions must not retroactively change hashes of frozen older
    # datasets by materializing newly added default fields.  New revisions
    # explicitly serialize their fields, so exclude_unset preserves both
    # backward compatibility and complete v2 coverage.
    payload = case.model_dump(
        mode="json", exclude={"hashes"}, exclude_unset=True
    )
    return sha256_canonical_json(payload["input"]), sha256_canonical_json(payload)


def validate_case_hashes(case: InterviewQualityCase) -> None:
    source_sha256, content_sha256 = expected_case_hashes(case)
    if case.hashes.source_sha256 != source_sha256:
        raise ValueError(f"source_sha256 mismatch for {case.case_id}")
    if case.hashes.content_sha256 != content_sha256:
        raise ValueError(f"content_sha256 mismatch for {case.case_id}")


def load_interview_quality_dataset(path: Path | str) -> InterviewQualityDataset:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    dataset = InterviewQualityDataset.model_validate(payload)
    for case in dataset.cases:
        validate_case_hashes(case)
    return dataset
