import json

from app.agents.knowledge import KnowledgeAgent
from app.services.prep import (
    InterviewPlan,
    InterviewQuestion,
    KnowledgeEvidenceRef,
    PrepContext,
    prepare_interview,
)
from app.services.vector_store import KnowledgeChunk
from tests.knowledge_repository_fakes import (
    FailingKnowledgeRepository,
    InMemoryKnowledgeRepository,
)


MANIFEST_HASH = "f" * 64


def make_chunk(
    chunk_id: str,
    *,
    title: str,
    domain: str,
    tags: list[str],
    score: float,
    content: str = "Internal benchmark answer that must not reach the provider.",
    source_type: str = "theory",
) -> KnowledgeChunk:
    return KnowledgeChunk(
        chunk_id=chunk_id,
        title=title,
        content=content,
        source_type=source_type,
        domain=domain,
        tags=tags,
        metadata={
            "content_sha256": (chunk_id[0] * 64),
            "corpus_manifest_sha256": MANIFEST_HASH,
            "content_kind": "mechanism",
        },
        score=score,
    )


class GroundedPlanLLM:
    def __init__(self, plan: InterviewPlan | None = None) -> None:
        self.plan = plan or InterviewPlan(
            title="Provider grounded plan",
            questions=[
                InterviewQuestion(
                    id="q1",
                    kind="technical",
                    prompt="Explain Redis cache consistency.",
                    focus="Redis consistency",
                ),
                InterviewQuestion(
                    id="q2",
                    kind="technical",
                    prompt="Explain Kafka delivery and retries.",
                    focus="Kafka delivery",
                ),
            ],
        )
        self.knowledge_context = None
        self.calls = 0

    def generate_plan(
        self,
        job_description: str,
        resume_text: str,
        knowledge_context: list[dict] | None = None,
    ) -> InterviewPlan:
        self.calls += 1
        self.knowledge_context = knowledge_context
        return self.plan


def make_repository() -> InMemoryKnowledgeRepository:
    return InMemoryKnowledgeRepository(
        [
            make_chunk(
                "redis_consistency",
                title="Redis Cache Consistency",
                domain="redis",
                tags=["redis", "cache"],
                score=0.91,
            ),
            make_chunk(
                "redis_backend",
                title="Redis Backend Benchmark",
                domain="redis",
                tags=["redis", "backend"],
                score=0.86,
                source_type="expert_benchmark",
            ),
            make_chunk(
                "kafka_delivery",
                title="Kafka Delivery Semantics",
                domain="kafka",
                tags=["kafka", "delivery"],
                score=0.93,
            ),
        ]
    )


def test_grounded_agent_binds_each_question_to_relevant_trusted_candidates():
    repository = make_repository()
    llm = GroundedPlanLLM()

    plan = KnowledgeAgent(llm=llm, vector_store=repository).generate_plan(
        job_description="Backend Engineer using Redis and Kafka.",
        resume_text="Built Redis caching and Kafka consumers.",
    )

    context = plan.prep_context
    assert context.schema_version == "v2"
    assert context.knowledge_status == "completed"
    assert {item.evidence_id for item in context.evidence_refs} == {
        "redis_consistency",
        "redis_backend",
        "kafka_delivery",
    }
    hints = {hint.question_id: hint for hint in context.question_hints}
    assert 1 <= len(hints["q1"].evidence_ids) <= 3
    assert set(hints["q1"].evidence_ids) <= {
        "redis_consistency",
        "redis_backend",
    }
    assert hints["q2"].evidence_ids == ["kafka_delivery"]
    assert all(
        evidence_id in {item.evidence_id for item in context.evidence_refs}
        for hint in context.question_hints
        for evidence_id in hint.evidence_ids
    )
    snapshot = context.binding_snapshot
    assert snapshot.corpus_manifest_sha256 == MANIFEST_HASH
    assert {
        evidence_id
        for query in snapshot.queries
        for evidence_id in query.hit_ids
    } == {item.evidence_id for item in context.evidence_refs}
    assert all(query.hit_content_sha256 for query in snapshot.queries)
    assert len(repository.search_calls) == 2


def test_provider_receives_only_safe_candidate_metadata_not_chunk_content():
    llm = GroundedPlanLLM()

    KnowledgeAgent(llm=llm, vector_store=make_repository()).generate_plan(
        job_description="Backend Engineer using Redis and Kafka.",
        resume_text="Built Redis caching and Kafka consumers.",
    )

    serialized = json.dumps(llm.knowledge_context, ensure_ascii=False)
    assert "redis_consistency" in serialized
    assert "Redis Cache Consistency" in serialized
    assert "Internal benchmark answer" not in serialized
    assert "content_sha256" not in serialized


def test_provider_cannot_invent_evidence_or_override_repository_score():
    malicious_plan = InterviewPlan(
        title="Provider plan",
        questions=[
            InterviewQuestion(
                id="q1",
                kind="technical",
                prompt="Explain Redis consistency.",
                focus="Redis",
            )
        ],
        prep_context=PrepContext(
            schema_version="v2",
            summary="Provider controlled context",
            knowledge_status="completed",
            evidence_refs=[
                KnowledgeEvidenceRef(
                    evidence_id="invented-id",
                    title="Invented",
                    domain="redis",
                    source_type="theory",
                    score=1.0,
                    content_sha256="x" * 64,
                    corpus_manifest_sha256="y" * 64,
                    candidate_summary="Invented",
                )
            ],
        ),
    )

    plan = KnowledgeAgent(
        llm=GroundedPlanLLM(malicious_plan),
        vector_store=make_repository(),
    ).generate_plan(
        job_description="Backend Engineer using Redis.",
        resume_text="Built Redis caching.",
    )

    evidence = {item.evidence_id: item for item in plan.prep_context.evidence_refs}
    assert "invented-id" not in evidence
    assert evidence["redis_consistency"].score == 0.91


def test_retrieval_failure_preserves_provider_plan_and_marks_degraded():
    llm = GroundedPlanLLM()

    plan = KnowledgeAgent(
        llm=llm,
        vector_store=FailingKnowledgeRepository(),
    ).generate_plan(
        job_description="Backend Engineer using Redis.",
        resume_text="Built Redis caching.",
    )

    assert llm.calls == 1
    assert plan.title == "Provider grounded plan"
    assert plan.prep_context.schema_version == "v2"
    assert plan.prep_context.knowledge_status == "degraded"
    assert plan.prep_context.evidence_refs == []
    assert all(not hint.evidence_ids for hint in plan.prep_context.question_hints)
    assert plan.prep_context.binding_snapshot.degraded_reason == "knowledge_unavailable"


def test_empty_retrieval_has_no_fabricated_references():
    plan = KnowledgeAgent(
        llm=GroundedPlanLLM(),
        vector_store=InMemoryKnowledgeRepository(),
    ).generate_plan(
        job_description="Backend Engineer using Redis.",
        resume_text="Built Redis caching.",
    )

    assert plan.prep_context.knowledge_status == "empty"
    assert plan.prep_context.evidence_refs == []
    assert all(not hint.evidence_ids for hint in plan.prep_context.question_hints)


def test_prepare_service_does_not_replace_provider_plan_when_knowledge_fails():
    plan = prepare_interview(
        job_description="Backend Engineer using Redis.",
        resume_text="Built Redis caching.",
        llm=GroundedPlanLLM(),
        knowledge_store=FailingKnowledgeRepository(),
    )

    assert plan.title == "Provider grounded plan"
    assert plan.prep_context.knowledge_status == "degraded"
    assert plan.prep_context.binding_snapshot.degraded_reason == "knowledge_unavailable"


def test_grounded_agent_keeps_two_argument_plan_provider_compatible():
    class LegacyPlanLLM:
        def generate_plan(self, job_description: str, resume_text: str) -> InterviewPlan:
            return GroundedPlanLLM().plan

    plan = KnowledgeAgent(
        llm=LegacyPlanLLM(),
        vector_store=make_repository(),
    ).generate_plan(
        job_description="Backend Engineer using Redis and Kafka.",
        resume_text="Built Redis caching and Kafka consumers.",
    )

    assert plan.title == "Provider grounded plan"
    assert plan.prep_context.schema_version == "v2"
