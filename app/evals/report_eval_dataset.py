import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


Domain = Literal["redis", "mysql", "kafka", "system-design", "project"]
QualityLevel = Literal["strong", "medium", "incorrect", "off_topic", "empty"]
QuestionKind = Literal["technical", "system-design", "project", "behavioral"]
DimensionName = Literal[
    "breadth",
    "depth",
    "architecture",
    "engineering",
    "communication",
]

CORE_QUALITY_LEVELS = {"strong", "medium", "incorrect"}
TERMINAL_QUALITY_LEVELS = {"off_topic", "empty"}


class EvaluationReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chunk_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    content: str = Field(min_length=1)
    source_type: str = Field(min_length=1)
    domain: Domain
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    score: float = Field(ge=0, le=1)


class EvaluationCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(min_length=1)
    group_id: str = Field(min_length=1)
    domain: Domain
    quality_level: QualityLevel
    question_kind: QuestionKind
    question: str = Field(min_length=1)
    focus: str = Field(min_length=1)
    answer: str
    expected_score_range: tuple[int, int]
    expected_applicable_dimensions: list[DimensionName] = Field(min_length=1)
    required_observations: list[str] = Field(default_factory=list)
    required_missing_points: list[str] = Field(default_factory=list)
    forbidden_claims: list[str] = Field(default_factory=list)
    reference: EvaluationReference

    @field_validator("expected_score_range", mode="before")
    @classmethod
    def validate_score_range_shape(cls, value: object) -> object:
        if not isinstance(value, (list, tuple)) or len(value) != 2:
            raise ValueError("expected_score_range must contain exactly two values")
        if any(isinstance(item, bool) or not isinstance(item, int) for item in value):
            raise ValueError("expected_score_range values must be integers")
        return value

    @field_validator("expected_applicable_dimensions")
    @classmethod
    def validate_unique_dimensions(
        cls, value: list[DimensionName]
    ) -> list[DimensionName]:
        if len(value) != len(set(value)):
            raise ValueError("expected_applicable_dimensions must be unique")
        return value

    @model_validator(mode="after")
    def validate_case_contract(self):
        low, high = self.expected_score_range
        if not 0 <= low <= high <= 100:
            raise ValueError(
                "expected_score_range must contain two ordered values from 0 to 100"
            )
        if self.reference.domain != self.domain:
            raise ValueError("reference domain must match case domain")
        return self


class EvaluationDataset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str = Field(min_length=1)
    cases: list[EvaluationCase] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_dataset_contract(self):
        case_ids = [case.case_id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("duplicate case_id")

        for group_id, cases in self.grouped_cases().items():
            levels = [case.quality_level for case in cases]
            if (
                len(cases) != 4
                or not CORE_QUALITY_LEVELS.issubset(levels)
                or sum(level in TERMINAL_QUALITY_LEVELS for level in levels) != 1
                or len(levels) != len(set(levels))
            ):
                raise ValueError(
                    f"group must contain strong, medium, incorrect, and exactly one "
                    f"off_topic or empty case: {group_id}"
                )
            if len({case.domain for case in cases}) != 1:
                raise ValueError(f"group cases must share one domain: {group_id}")
        return self

    def grouped_cases(self) -> dict[str, list[EvaluationCase]]:
        grouped: defaultdict[str, list[EvaluationCase]] = defaultdict(list)
        for case in self.cases:
            grouped[case.group_id].append(case)
        return dict(grouped)

    def target_attempt_count(self, *, runs_per_case: int) -> int:
        if isinstance(runs_per_case, bool) or not isinstance(runs_per_case, int):
            raise ValueError("runs_per_case must be a positive integer")
        if runs_per_case <= 0:
            raise ValueError("runs_per_case must be a positive integer")
        return len(self.cases) * runs_per_case


def load_evaluation_dataset(path: Path) -> EvaluationDataset:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return EvaluationDataset.model_validate(payload)
