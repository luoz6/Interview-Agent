from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


CalibrationLanguage = Literal["zh", "en", "mixed"]
CalibrationQuestionType = Literal[
    "technical",
    "system_design",
    "project_review",
    "behavioral",
]
CalibrationQuality = Literal["strong", "medium", "incorrect", "off_topic", "empty"]
CalibrationPartition = Literal["dev", "blind"]
ReviewStatus = Literal["pending", "approved", "disputed"]


class CalibrationAnnotation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    author_id: str = Field(min_length=1)
    reviewer_id: str | None = None
    review_status: ReviewStatus
    rationale: str = Field(min_length=1)
    dispute_resolution: str | None = None

    @model_validator(mode="after")
    def validate_review(self):
        if self.review_status == "approved" and not self.reviewer_id:
            raise ValueError("approved calibration cases require an independent reviewer")
        if self.review_status == "disputed" and not self.dispute_resolution:
            raise ValueError("disputed calibration cases require a resolution")
        return self


class CalibrationCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str = Field(min_length=1)
    group_id: str = Field(min_length=1)
    partition: CalibrationPartition
    language: CalibrationLanguage
    question_type: CalibrationQuestionType
    quality_label: CalibrationQuality
    question: str = Field(min_length=1)
    answer: str
    expected_score_range: tuple[int, int]
    required_evidence: list[str] = Field(default_factory=list)
    required_missing_points: list[str] = Field(default_factory=list)
    forbidden_claims: list[str] = Field(default_factory=list)
    error_tags: list[str] = Field(default_factory=list)
    source_classification: Literal["synthetic"] = "synthetic"
    contains_real_candidate_data: Literal[False] = False
    contains_principal_memory: Literal[False] = False
    annotation: CalibrationAnnotation

    @model_validator(mode="after")
    def validate_case(self):
        low, high = self.expected_score_range
        if not 0 <= low <= high <= 100:
            raise ValueError("expected_score_range must be ordered within 0..100")
        if self.quality_label in {"strong", "medium"} and not self.required_evidence:
            raise ValueError("strong and medium cases require expected evidence")
        if self.quality_label == "medium" and not self.required_missing_points:
            raise ValueError("medium cases require annotated missing points")
        if self.quality_label == "strong" and self.required_missing_points:
            raise ValueError("strong cases cannot declare required missing points")
        return self


class CalibrationDataset(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["report-score-calibration-v1"]
    dataset_id: str = Field(min_length=1)
    dataset_version: str = Field(min_length=1)
    description: str = Field(min_length=1)
    blind_policy: str = Field(min_length=1)
    cases: list[CalibrationCase] = Field(min_length=60, max_length=100)

    @model_validator(mode="after")
    def validate_dataset(self):
        case_ids = [case.case_id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("duplicate calibration case_id")
        groups: dict[str, list[CalibrationCase]] = defaultdict(list)
        for case in self.cases:
            groups[case.group_id].append(case)
        for group_id, members in groups.items():
            labels = {case.quality_label for case in members}
            if len(members) != 4 or not {"strong", "medium", "incorrect"}.issubset(labels):
                raise ValueError(f"calibration group is incomplete: {group_id}")
            if len(labels.intersection({"off_topic", "empty"})) != 1:
                raise ValueError(f"calibration group needs one terminal case: {group_id}")
        for field_name in ("language", "question_type", "quality_label"):
            counts = Counter(str(getattr(case, field_name)) for case in self.cases)
            if any(count < 8 for count in counts.values()):
                raise ValueError(f"every {field_name} stratum requires at least 8 cases")
        if sum(case.partition == "blind" for case in self.cases) < 20:
            raise ValueError("blind partition requires at least 20 cases")
        return self

    @property
    def review_status(self) -> str:
        statuses = {case.annotation.review_status for case in self.cases}
        if statuses == {"approved"}:
            return "APPROVED"
        if "disputed" in statuses:
            return "DISPUTED"
        return "PENDING_INDEPENDENT_REVIEW"

    @property
    def gate_eligible(self) -> bool:
        return self.review_status == "APPROVED"

    def require_gate_eligible(self) -> None:
        if not self.gate_eligible:
            raise ValueError(
                "calibration dataset is not gate eligible: independent review is incomplete"
            )


def load_calibration_dataset(path: Path | str) -> CalibrationDataset:
    return CalibrationDataset.model_validate_json(Path(path).read_text(encoding="utf-8"))


def calibration_dataset_sha256(path: Path | str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def canonical_calibration_sha256(dataset: CalibrationDataset) -> str:
    payload = dataset.model_dump(mode="json")
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
