from app.services.knowledge_grounding import (
    attach_grounded_prep_context,
    provider_knowledge_context,
    retrieve_grounding,
)
from app.services.knowledge_query import build_knowledge_queries
from app.services.knowledge_profile import build_role_profile
from app.services.prep import InterviewPlan, InterviewQuestion
from app.services.vector_store import KnowledgeChunk


class Repository:
    def __init__(self, chunks):
        self.chunks = chunks

    def search(self, query, *, job_tags, source_types, limit):
        return self.chunks[:limit]


def make_chunk():
    return KnowledgeChunk(
        chunk_id="postgresql_indexing",
        title="PostgreSQL 索引设计",
        content="内部正文不应进入摘要",
        source_type="theory",
        domain="postgresql",
        tags=["postgresql", "database"],
        metadata={
            "content_sha256": "a" * 64,
            "corpus_manifest_sha256": "b" * 64,
            "content_kind": "mechanism",
        },
        score=0.9,
    )


def test_grounding_summaries_are_chinese():
    profile = build_role_profile(
        "高级后端工程师，负责 PostgreSQL。", "参与 PostgreSQL 服务治理。"
    )
    result = retrieve_grounding(
        build_knowledge_queries(profile), Repository([make_chunk()])
    )
    context = provider_knowledge_context(result)
    assert context[0]["candidate_summary"] == (
        "PostgreSQL 索引设计提供用于 PostgreSQL 面试判断的机制证据。"
    )
    assert all(
        phrase not in context[0]["candidate_summary"]
        for phrase in ("Retrieved", "No trusted", "provides evidence")
    )

    plan = InterviewPlan(
        title="面试计划",
        questions=[
            InterviewQuestion(
                id="q1", kind="technical", prompt="如何设计索引？", focus="PostgreSQL"
            )
        ],
    )
    grounded = attach_grounded_prep_context(
        plan, role_profile=profile, result=result, prep_run_id="prep-test"
    )
    summary = grounded.prep_context.topics[0].evidence
    assert summary == "已为PostgreSQL找到 1 条可信知识证据。"
    assert grounded.prep_context.summary == (
        "知识智能体预热了 1 条可信知识证据，并为 1 道题绑定了提问依据。"
    )
    assert all(
        phrase not in summary for phrase in ("Retrieved", "No trusted", "provides evidence")
    )


def test_empty_grounding_summary_is_chinese():
    profile = build_role_profile("高级后端工程师，负责 PostgreSQL。", "")
    result = retrieve_grounding(build_knowledge_queries(profile), Repository([]))
    plan = InterviewPlan(
        title="面试计划",
        questions=[
            InterviewQuestion(id="q1", kind="technical", prompt="问题", focus="PostgreSQL")
        ],
    )
    grounded = attach_grounded_prep_context(
        plan, role_profile=profile, result=result, prep_run_id="prep-test"
    )
    summary = grounded.prep_context.topics[0].evidence
    assert summary == "未找到可用于PostgreSQL的可信知识证据。"


def test_degraded_grounding_context_summary_is_chinese():
    profile = build_role_profile("高级后端工程师，负责 PostgreSQL。", "")
    queries = build_knowledge_queries(profile)

    class FailingRepository:
        def search(self, query, *, job_tags, source_types, limit):
            raise RuntimeError("不可用")

    result = retrieve_grounding(queries, FailingRepository())
    plan = InterviewPlan(
        title="面试计划",
        questions=[
            InterviewQuestion(id="q1", kind="technical", prompt="问题", focus="PostgreSQL")
        ],
    )
    grounded = attach_grounded_prep_context(
        plan, role_profile=profile, result=result, prep_run_id="prep-test"
    )

    assert grounded.prep_context.summary == (
        "知识检索已降级，模型服务生成的面试计划仍可使用。"
    )
    assert "Provider" not in grounded.prep_context.summary
