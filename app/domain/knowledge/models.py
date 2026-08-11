from typing import Any

from pydantic import BaseModel, Field


DEFAULT_SOURCE_TYPES = ["theory", "engineering_guide", "expert_benchmark"]


class KnowledgeChunk(BaseModel):
    chunk_id: str
    title: str
    content: str
    source_type: str
    domain: str
    tags: list[str]
    metadata: dict[str, Any]
    score: float | None = None


class KnowledgeQuery(BaseModel):
    query_id: str
    topic_id: str
    query_text: str
    canonical_tag: str
    filters: dict[str, list[str]] = Field(default_factory=dict)
    source_types: list[str] = Field(default_factory=lambda: list(DEFAULT_SOURCE_TYPES))
    top_k: int = 5
