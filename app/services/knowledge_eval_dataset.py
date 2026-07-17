from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator


DEFAULT_DATASET_PATH = Path("tests/golden/knowledge_retrieval_v1.json")


class KnowledgeRetrievalCase(BaseModel):
    case_id: str = Field(min_length=1)
    category: Literal["relevant", "weak_keyword", "negative"]
    domain: Literal["redis", "fastapi", "mysql", "kafka", "system-design"]
    query_text: str = Field(min_length=1, max_length=500)
    canonical_tags: list[str] = Field(min_length=1)
    source_types: list[str] = Field(
        default_factory=lambda: [
            "theory",
            "engineering_guide",
            "expert_benchmark",
        ]
    )
    relevant_chunk_ids: list[str] = Field(default_factory=list)
    top_k: int = Field(default=3, ge=1, le=10)

    @model_validator(mode="after")
    def validate_reference_contract(self):
        if self.category == "negative" and self.relevant_chunk_ids:
            raise ValueError("negative case cannot declare relevant chunks")
        if self.category != "negative" and not self.relevant_chunk_ids:
            raise ValueError("positive case requires relevant chunks")
        return self


class KnowledgeRetrievalDataset(BaseModel):
    version: str = Field(min_length=1)
    cases: list[KnowledgeRetrievalCase] = Field(min_length=1)

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

    def validate_rc_shape(self) -> None:
        category_counts = {
            category: sum(case.category == category for case in self.cases)
            for category in ("relevant", "weak_keyword", "negative")
        }
        if len(self.cases) < 30:
            raise ValueError("knowledge retrieval RC dataset requires at least 30 cases")
        if category_counts["relevant"] < 20:
            raise ValueError("knowledge retrieval RC dataset requires 20 relevant cases")
        if category_counts["weak_keyword"] < 5:
            raise ValueError("knowledge retrieval RC dataset requires 5 weak keyword cases")
        if category_counts["negative"] < 5:
            raise ValueError("knowledge retrieval RC dataset requires 5 negative cases")


def load_knowledge_retrieval_dataset(
    path: Path | str = DEFAULT_DATASET_PATH,
) -> KnowledgeRetrievalDataset:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    dataset = KnowledgeRetrievalDataset.model_validate(payload)
    dataset.validate_rc_shape()
    return dataset
