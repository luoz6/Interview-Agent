import json
from datetime import datetime, timezone

import pytest

from app.domain.knowledge.source_scope import (
    SelectedUserDocumentRevision,
    build_knowledge_source_scope,
)
from app.services.interview_plan_revision import (
    build_interview_knowledge_scope_snapshot,
)

from app.services.knowledge_grounding import (
    attach_grounded_prep_context,
    provider_knowledge_context,
    retrieve_grounding,
)
from app.services.knowledge_query import build_knowledge_queries
from app.services.knowledge_profile import build_role_profile
from app.services.prep import (
    InterviewPlan,
    InterviewQuestion,
    public_interview_plan_payload,
)
from app.domain.knowledge.models import KnowledgeChunk


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
    snapshot = grounded.prep_context.binding_snapshot
    bundle = snapshot.base_evidence_bundle
    binding = snapshot.question_evidence_bindings[0]
    assert bundle.prep_run_id == "prep-test"
    assert bundle.corpus_manifest_sha256 == "b" * 64
    assert [ref.evidence_id for ref in bundle.candidate_evidence_refs] == [
        "postgresql_indexing"
    ]
    assert binding.bundle_id == bundle.bundle_id
    assert binding.question_id == "q1"
    assert binding.selected_evidence_ids == ("postgresql_indexing",)
    serialized = json.dumps(bundle.model_dump(mode="json"), ensure_ascii=False)
    assert "鍐呴儴姝ｆ枃涓嶅簲杩涘叆鎽樿" not in serialized
    assert all(
        retrieval.query.query_text not in serialized
        for retrieval in result.retrievals
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


def test_invalid_sha256_metadata_degrades_before_bundle_persistence():
    chunk = make_chunk().model_copy(
        update={
            "metadata": {
                **make_chunk().metadata,
                "content_sha256": "not-a-real-sha256",
            }
        }
    )
    profile = build_role_profile("PostgreSQL backend engineer", "PostgreSQL")

    result = retrieve_grounding(
        build_knowledge_queries(profile), Repository([chunk])
    )
    plan = InterviewPlan(
        title="Interview",
        questions=[
            InterviewQuestion(
                id="q1",
                kind="technical",
                prompt="Explain PostgreSQL indexing",
                focus="PostgreSQL",
            )
        ],
    )
    grounded = attach_grounded_prep_context(
        plan,
        role_profile=profile,
        result=result,
        prep_run_id="prep-invalid-hash",
    )

    assert result.status == "degraded"
    assert result.degraded_reason == "invalid_knowledge_metadata"
    assert grounded.prep_context.binding_snapshot.base_evidence_bundle.candidate_evidence_refs == ()


SCOPE_OWNER = "principal-question-binding"
SCOPE_DOCUMENT_ID = "00000000-0000-0000-0000-000000000001"
SCOPE_REVISION_ID = "00000000-0000-0000-0000-000000000011"
SCOPE_CONTENT_SHA256 = "d" * 64


def _question_source_scope(*, include_system, include_user):
    selected_documents = (
        (
            SelectedUserDocumentRevision(
                document_id=SCOPE_DOCUMENT_ID,
                document_revision_id=SCOPE_REVISION_ID,
                content_sha256=SCOPE_CONTENT_SHA256,
                allowed_usages=("question", "feedback"),
            ),
        )
        if include_user
        else ()
    )
    snapshot = build_interview_knowledge_scope_snapshot(
        include_system_knowledge=include_system,
        selected_documents=selected_documents,
        created_at=datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc),
    )
    return build_knowledge_source_scope(
        snapshot,
        owner_principal_id=SCOPE_OWNER if include_user else None,
        usage="question",
    )


@pytest.mark.parametrize(
    ("source_scope", "expected_kind"),
    [
        (None, "legacy_unscoped"),
        (
            _question_source_scope(include_system=False, include_user=False),
            "explicit_empty",
        ),
        (
            _question_source_scope(include_system=True, include_user=False),
            "system_only",
        ),
        (
            _question_source_scope(include_system=False, include_user=True),
            "user_only",
        ),
        (
            _question_source_scope(include_system=True, include_user=True),
            "mixed",
        ),
    ],
)
def test_question_binding_freezes_safe_source_scope_kind_and_digest(
    source_scope,
    expected_kind,
):
    profile = build_role_profile("Backend engineer", "")
    result = retrieve_grounding(build_knowledge_queries(profile), Repository([]))
    plan = InterviewPlan(
        title="Scoped interview",
        questions=[
            InterviewQuestion(
                id="q1",
                kind="technical",
                prompt="Explain bounded retries",
                focus="reliability",
            )
        ],
    )

    grounded = attach_grounded_prep_context(
        plan,
        role_profile=profile,
        result=result,
        prep_run_id="prep-source-scope",
        source_scope=source_scope,
    )

    question_binding = (
        grounded.prep_context.binding_snapshot.question_evidence_bindings[0]
    )
    if source_scope is None:
        assert question_binding.source_scope_binding is None
    else:
        frozen = question_binding.source_scope_binding
        assert frozen.scope_kind == expected_kind
        assert frozen.usage == "question"
        assert frozen.source_scope_sha256 == source_scope.source_scope_sha256
        internal = json.dumps(
            frozen.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
        )
        for forbidden in (
            SCOPE_OWNER,
            SCOPE_DOCUMENT_ID,
            SCOPE_REVISION_ID,
            SCOPE_CONTENT_SHA256,
            "owner_principal_id",
            "document_revision_id",
            "content_sha256",
        ):
            assert forbidden not in internal

    public = json.dumps(
        public_interview_plan_payload(grounded),
        ensure_ascii=False,
        sort_keys=True,
    )
    assert "source_scope_binding" not in public
    assert "source_scope_sha256" not in public
    assert SCOPE_OWNER not in public
    assert SCOPE_REVISION_ID not in public
    assert SCOPE_CONTENT_SHA256 not in public
