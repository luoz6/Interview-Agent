from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class KnowledgeReviewStatus(StrEnum):
    DRAFT = "draft"
    REVIEWED = "reviewed"
    APPROVED = "approved"
    RETIRED = "retired"


class EvaluationLevel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    level: str = Field(min_length=1)
    required_signals: tuple[str, ...] = ()
    optional_signals: tuple[str, ...] = ()


class KnowledgeUnit(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    knowledge_unit_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,127}$")
    domain: str = Field(min_length=1)
    topic: str = Field(min_length=1)
    aliases: tuple[str, ...] = ()
    technical_terms: tuple[str, ...] = ()
    expected_signals: tuple[str, ...] = Field(min_length=1)
    failure_modes: tuple[str, ...] = ()
    hard_negatives: tuple[str, ...] = ()
    weak_answer_signals: tuple[str, ...] = ()
    expert_signals: tuple[str, ...] = ()
    follow_up_triggers: tuple[str, ...] = ()
    evaluation_levels: tuple[EvaluationLevel, ...] = ()
    source_references: tuple[str, ...] = ()
    review_status: KnowledgeReviewStatus = KnowledgeReviewStatus.DRAFT
    schema_version: str = "knowledge-unit-v2"

    @model_validator(mode="after")
    def validate_unique_signals(self):
        for field_name in (
            "aliases",
            "technical_terms",
            "expected_signals",
            "failure_modes",
            "hard_negatives",
            "weak_answer_signals",
            "expert_signals",
            "follow_up_triggers",
            "source_references",
        ):
            values = getattr(self, field_name)
            normalized = [value.strip().casefold() for value in values]
            if any(not value for value in normalized):
                raise ValueError(f"{field_name} cannot contain blank values")
            if len(normalized) != len(set(normalized)):
                raise ValueError(f"{field_name} cannot contain duplicate values")
        known = set(self.expected_signals) | set(self.expert_signals)
        for level in self.evaluation_levels:
            unknown = set(level.required_signals) - known
            if unknown:
                raise ValueError(
                    f"evaluation level references unknown signals: {sorted(unknown)}"
                )
        return self

    def required_signals_for(self, level: str | None) -> tuple[str, ...]:
        if level:
            match = next(
                (item for item in self.evaluation_levels if item.level == level), None
            )
            if match is not None and match.required_signals:
                return match.required_signals
        return self.expected_signals
