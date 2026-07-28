from app.services.knowledge_profile import build_role_profile
from app.services.knowledge_query import build_knowledge_queries


def test_build_queries_is_deterministic_and_has_stable_unique_ids():
    profile = build_role_profile(
        "Senior Backend Engineer building FastAPI, Redis, Kafka, and PostgreSQL services.",
        "Delivered FastAPI APIs with Redis and Kafka.",
    )

    first = build_knowledge_queries(profile)
    second = build_knowledge_queries(profile)

    assert first == second
    assert [query.topic_id for query in first] == [
        "topic-fastapi",
        "topic-redis",
        "topic-postgresql",
        "topic-kafka",
    ]
    assert len({query.query_id for query in first}) == len(first)
    assert all(query.query_id.startswith("kq-") for query in first)
    assert [query.filters["tags"] for query in first] == [
        ["fastapi"],
        ["redis"],
        ["postgresql"],
        ["kafka"],
    ]


def test_queries_exclude_pii_resume_text_and_uncovered_technologies():
    profile = build_role_profile(
        "Backend Engineer using FastAPI and PostgreSQL. Contact hiring@example.com.",
        "Alice, +86 13800138000, https://example.com/alice, built FastAPI APIs.",
    )

    queries = build_knowledge_queries(profile)
    serialized = " ".join(query.model_dump_json() for query in queries)

    assert [query.topic_id for query in queries] == ["topic-fastapi", "topic-postgresql"]
    assert "hiring@example.com" not in serialized
    assert "13800138000" not in serialized
    assert "example.com" not in serialized
    assert "Alice" not in serialized
    assert "postgresql" in serialized.lower()


def test_empty_profile_produces_no_queries():
    profile = build_role_profile("", "")

    assert build_knowledge_queries(profile) == []


def test_query_text_is_normalized_and_bounded():
    profile = build_role_profile(
        "Senior Backend Engineer responsible for Redis cache consistency.",
        "Built Redis cache systems.",
    )

    query = build_knowledge_queries(profile)[0]

    assert query.query_text == "后端工程师 | 高级 | Redis | 缓存 | 面试知识证据"
    assert query.top_k == 5
    assert query.source_types == [
        "theory",
        "engineering_guide",
        "expert_benchmark",
    ]
    assert len(query.query_text) <= 240


def test_query_text_uses_chinese_natural_language():
    profile = build_role_profile(
        "高级后端工程师，负责 PostgreSQL 和系统可靠性。",
        "参与 PostgreSQL 服务治理。",
    )
    queries = build_knowledge_queries(profile)

    assert [item.topic_id for item in queries] == [
        "topic-postgresql",
        "topic-reliability",
    ]
    assert queries[0].query_text == "后端工程师 | 高级 | PostgreSQL | 数据库 | 面试知识证据"
    assert "interview evidence" not in " ".join(item.query_text for item in queries)
