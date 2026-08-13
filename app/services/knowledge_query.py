from __future__ import annotations

import hashlib
import json
import re

from app.domain.knowledge.models import DEFAULT_SOURCE_TYPES, KnowledgeQuery
from app.services.knowledge_profile import CANONICAL_TAXONOMY
from app.services.prep import RoleProfile


QUERYABLE_TOPIC_TAGS = {
    "python",
    "fastapi",
    "redis",
    "mysql",
    "postgresql",
    "rocketmq",
    "system-design",
    "reliability",
}
QUERY_DOMAIN_LABELS = {
    "python": "后端开发",
    "fastapi": "后端开发",
    "redis": "缓存",
    "mysql": "数据库",
    "postgresql": "数据库",
    "rocketmq": "消息系统",
    "system-design": "系统设计",
    "reliability": "可靠性",
}
SENIORITY_LABELS = {
    "principal": "专家级",
    "staff": "专家级",
    "lead": "负责人级",
    "senior": "高级",
    "mid": "中级",
    "junior": "初级",
}
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
    role = _controlled_role_label(role_profile.role_title)
    parts = [
        role,
        SENIORITY_LABELS.get(role_profile.seniority, ""),
        CANONICAL_TAXONOMY[tag]["label"],
        QUERY_DOMAIN_LABELS[tag],
        "面试知识证据",
    ]
    normalized: list[str] = []
    for part in parts:
        value = re.sub(r"\s+", " ", part.strip())
        if value and value not in normalized:
            normalized.append(value)
    return " | ".join(normalized)[:240].rstrip()


def _controlled_role_label(role_title: str) -> str:
    value = role_title.lower()
    role_labels = (
        (("backend", "后端"), "后端工程师"),
        (("frontend", "front end", "前端"), "前端工程师"),
        (("full stack", "fullstack", "全栈"), "全栈工程师"),
        (("data", "数据"), "数据工程师"),
        (("machine learning", "ml", "算法"), "算法工程师"),
        (("devops", "运维"), "运维工程师"),
        (("security", "安全"), "安全工程师"),
        (("qa", "test", "测试"), "测试工程师"),
        (("platform", "平台"), "平台工程师"),
        (("software", "软件"), "软件工程师"),
    )
    for aliases, label in role_labels:
        if any(alias in value for alias in aliases):
            return label
    return "技术岗位"
