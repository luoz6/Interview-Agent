from __future__ import annotations

import hashlib
import json
import re

from pydantic import BaseModel, Field

from app.services.knowledge_profile import CANONICAL_TAXONOMY
from app.services.prep import RoleProfile


QUERYABLE_TOPIC_TAGS = {"fastapi", "redis", "mysql", "kafka", "system-design"}
DEFAULT_SOURCE_TYPES = ["theory", "expert_benchmark"]


class KnowledgeQuery(BaseModel):
    query_id: str
    topic_id: str
    query_text: str
    canonical_tag: str
    filters: dict[str, list[str]] = Field(default_factory=dict)
    source_types: list[str] = Field(default_factory=lambda: list(DEFAULT_SOURCE_TYPES))
    top_k: int = 5


def build_knowledge_queries(role_profile: RoleProfile) -> list[KnowledgeQuery]:
    queries: list[KnowledgeQuery] = []
    for tag in role_profile.canonical_tags:
        if tag not in QUERYABLE_TOPIC_TAGS or tag not in CANONICAL_TAXONOMY:
            continue
        topic_id = f"topic-{tag}"
        query_text = _build_query_text(role_profile, tag)
        identity = {
            "canonical_tag": tag,
            "filters": {"tags": [tag]},
            "query_text": query_text,
            "source_types": DEFAULT_SOURCE_TYPES,
            "top_k": 5,
            "topic_id": topic_id,
        }
        digest = hashlib.sha256(
            json.dumps(identity, ensure_ascii=True, sort_keys=True).encode("utf-8")
        ).hexdigest()[:16]
        queries.append(
            KnowledgeQuery(
                query_id=f"kq-{digest}",
                topic_id=topic_id,
                query_text=query_text,
                canonical_tag=tag,
                filters={"tags": [tag]},
            )
        )
    return queries


def _build_query_text(role_profile: RoleProfile, tag: str) -> str:
    role = _base_role_title(role_profile.role_title)
    parts = [
        role,
        role_profile.seniority,
        tag,
        CANONICAL_TAXONOMY[tag]["domain"],
        "interview evidence",
    ]
    normalized: list[str] = []
    for part in parts:
        value = re.sub(r"\s+", " ", part.strip().lower())
        if value and value not in normalized:
            normalized.append(value)
    return " | ".join(normalized)[:240].rstrip()


def _base_role_title(role_title: str) -> str:
    value = role_title.lower()
    value = re.sub(
        r"\b(?:principal|staff|lead|senior|sr\.?|mid(?:dle)?|junior|jr\.?)\b",
        " ",
        value,
    )
    return re.sub(r"\s+", " ", value).strip()
