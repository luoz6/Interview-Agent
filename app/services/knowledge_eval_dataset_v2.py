from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


DEFAULT_DATASET_V2_PILOT_PATH = Path(
    "tests/golden/knowledge_retrieval_v2_pilot.json"
)

EvaluationGroup = Literal[
    "fastapi",
    "redis",
    "relational-database",
    "kafka",
    "system-design",
    "reliability",
]
SourceType = Literal["theory", "engineering_guide", "expert_benchmark"]

EVALUATION_GROUP_DOMAIN_MAP: dict[str, set[str]] = {
    "fastapi": {"python", "fastapi"},
    "redis": {"redis"},
    "relational-database": {"mysql", "postgresql"},
    "kafka": {"kafka"},
    "system-design": {"system-design"},
    "reliability": {"reliability", "system-design"},
}

_CJK_CHARACTER = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")


class KnowledgeRetrievalCaseV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,127}$")
    evaluation_group: EvaluationGroup
    query_text: str = Field(min_length=1, max_length=500)
    canonical_tags: list[str] = Field(min_length=1)
    source_types: list[SourceType] = Field(min_length=1)
    allowed_domains: list[str] = Field(min_length=1)
    primary_relevant_chunk_ids: list[str] = Field(min_length=1)
    accepted_related_chunk_ids: list[str] = Field(default_factory=list)
    excluded_chunk_ids: list[str] = Field(default_factory=list)
    top_k: Literal[5] = 5

    @model_validator(mode="after")
    def validate_case_contract(self):
        if not _CJK_CHARACTER.search(self.query_text):
            raise ValueError("query_text must contain Chinese text")

        allowed_for_group = EVALUATION_GROUP_DOMAIN_MAP[self.evaluation_group]
        requested_domains = set(self.allowed_domains)
        if not requested_domains <= allowed_for_group:
            raise ValueError(
                "allowed_domains must belong to the evaluation group domain mapping"
            )

        list_fields = (
            "canonical_tags",
            "source_types",
            "allowed_domains",
            "primary_relevant_chunk_ids",
            "accepted_related_chunk_ids",
            "excluded_chunk_ids",
        )
        for field_name in list_fields:
            values = getattr(self, field_name)
            if len(values) != len(set(values)):
                raise ValueError(f"{field_name} cannot contain duplicate values")

        primary = set(self.primary_relevant_chunk_ids)
        accepted = set(self.accepted_related_chunk_ids)
        excluded = set(self.excluded_chunk_ids)
        if primary & accepted or primary & excluded or accepted & excluded:
            raise ValueError(
                "primary, accepted-related, and excluded chunk IDs must be disjoint"
            )
        return self


class KnowledgeRetrievalDatasetV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str = Field(min_length=1)
    cases: list[KnowledgeRetrievalCaseV2] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_cases(self):
        seen_ids: set[str] = set()
        seen_queries: set[str] = set()
        for case in self.cases:
            if case.case_id in seen_ids:
                raise ValueError(f"duplicate retrieval case id: {case.case_id}")
            seen_ids.add(case.case_id)

            normalized_query = " ".join(case.query_text.casefold().split())
            if normalized_query in seen_queries:
                raise ValueError(f"duplicate retrieval query: {case.query_text}")
            seen_queries.add(normalized_query)
        return self


def load_knowledge_retrieval_dataset_v2(
    path: Path | str = DEFAULT_DATASET_V2_PILOT_PATH,
    *,
    expected_case_count: int,
    manifest: dict,
) -> KnowledgeRetrievalDatasetV2:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    dataset = KnowledgeRetrievalDatasetV2.model_validate(payload)

    if expected_case_count < 1:
        raise ValueError("expected_case_count must be positive")
    if len(dataset.cases) != expected_case_count:
        raise ValueError(
            f"expected {expected_case_count} retrieval cases, found {len(dataset.cases)}"
        )

    known_ids = {
        item["chunk_id"]
        for item in manifest.get("chunks", [])
        if isinstance(item, dict) and isinstance(item.get("chunk_id"), str)
    }
    referenced_ids = {
        chunk_id
        for case in dataset.cases
        for chunk_id in (
            case.primary_relevant_chunk_ids
            + case.accepted_related_chunk_ids
            + case.excluded_chunk_ids
        )
    }
    missing_ids = sorted(referenced_ids - known_ids)
    if missing_ids:
        raise ValueError(f"dataset references missing chunk IDs: {missing_ids}")

    group_count = len(EVALUATION_GROUP_DOMAIN_MAP)
    if expected_case_count % group_count:
        raise ValueError("expected_case_count must be evenly distributed across six groups")
    expected_per_group = expected_case_count // group_count
    actual_per_group = {
        group: sum(case.evaluation_group == group for case in dataset.cases)
        for group in EVALUATION_GROUP_DOMAIN_MAP
    }
    if any(count != expected_per_group for count in actual_per_group.values()):
        raise ValueError(
            "retrieval cases must be evenly distributed across six evaluation groups"
        )

    return dataset
