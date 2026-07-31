from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


DEFAULT_MEMORY_QUALITY_DATASET = Path("tests/golden/memory_long_context_v1.json")


class MemoryQualityTurn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: str = Field(pattern=r"^q[0-9]{1,3}$")
    question: str = Field(min_length=1, max_length=500)
    answer: str = Field(min_length=1, max_length=2000)
    status: Literal["active", "deleted", "revoked", "skipped"] = "active"


class MemoryQualityClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,127}$")
    source_question_ids: list[str] = Field(min_length=1)
    summary: str = Field(min_length=1, max_length=1000)
    supporting_excerpts: list[str] = Field(min_length=1)
    unresolved_topics: list[str] = Field(default_factory=list)
    status: Literal["active", "superseded", "deleted"] = "active"


class MemoryQualityFact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fact_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,127}$")
    value: str = Field(min_length=1, max_length=500)
    unresolved: bool = False


class MemoryQualityCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,127}$")
    language_bucket: Literal["zh_hans", "en", "mixed"]
    principal_id: str = Field(pattern=r"^principal-[a-z0-9-]+$")
    turns: list[MemoryQualityTurn] = Field(min_length=20, max_length=50)
    corrections: list[MemoryQualityTurn] = Field(default_factory=list)
    question_memory_claims: list[MemoryQualityClaim] = Field(default_factory=list)
    expected_atomic_facts: list[MemoryQualityFact] = Field(min_length=1)
    superseded_atomic_values: list[str] = Field(default_factory=list)
    principal_memory_facts: list[str] = Field(default_factory=list)
    foreign_principal_facts: list[str] = Field(default_factory=list)
    scoring_evidence_sources: list[str] = Field(default_factory=list)
    current_question_id: str
    selectable_token_budget: int = Field(default=900, ge=100, le=20000)
    provider_input_cap: int = Field(default=4000, ge=100)
    provider_call_attempted: bool = True
    expected_route: Literal["deterministic", "memory", "fallback"]
    simulated_provider_failure: bool = False

    @model_validator(mode="after")
    def validate_case(self):
        question_ids = {turn.question_id for turn in self.turns}
        if self.current_question_id not in question_ids:
            raise ValueError("current_question_id must exist in turns")
        if len(question_ids) != len(self.turns):
            raise ValueError("base turn question IDs must be unique")
        if any(item.status == "active" for item in self.turns if item.answer == ""):
            raise ValueError("active turns require answers")
        if self.simulated_provider_failure and self.expected_route != "fallback":
            raise ValueError("provider failure cases must expect fallback")
        return self


class MemoryQualityDataset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal["memory-long-context-v1"]
    synthetic_only: Literal[True]
    cases: list[MemoryQualityCase] = Field(min_length=3)

    @model_validator(mode="after")
    def validate_coverage(self):
        languages = {case.language_bucket for case in self.cases}
        if languages != {"zh_hans", "en", "mixed"}:
            raise ValueError("dataset must cover zh_hans, en, and mixed")
        if len({case.case_id for case in self.cases}) != len(self.cases):
            raise ValueError("memory quality case IDs must be unique")
        return self


def load_memory_quality_dataset(
    path: Path | str = DEFAULT_MEMORY_QUALITY_DATASET,
) -> MemoryQualityDataset:
    return MemoryQualityDataset.model_validate(
        json.loads(Path(path).read_text(encoding="utf-8"))
    )
