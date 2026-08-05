from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


DatasetId = Literal[
    "initial-question-quality-v1",
    "followup-decision-quality-v1",
    "report-score-quality-v2",
    "report-semantic-quality-v1",
]

DATASET_CASE_TYPES = {
    "initial-question-quality-v1": "initial_question",
    "followup-decision-quality-v1": "followup_decision",
    "report-score-quality-v2": "report_score",
    "report-semantic-quality-v1": "report_semantic",
}


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
        if self.fixture_only and any(case.gate_eligible for case in self.cases):
            raise ValueError("contract-only fixtures cannot be gate eligible")
        return self


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
    payload = case.model_dump(mode="json", exclude={"hashes"})
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
