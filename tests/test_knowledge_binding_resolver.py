from app.services.knowledge_binding import KnowledgeBindingResolver
from app.services.prep import (
    InterviewPlan,
    InterviewQuestion,
    KnowledgeBindingSnapshot,
    KnowledgeEvidenceRef,
    PrepContext,
    PrepKnowledgeTopic,
    PrepQuestionHint,
)
from app.services.vector_store import KnowledgeChunk
from tests.knowledge_repository_fakes import InMemoryKnowledgeRepository


MANIFEST_HASH = "f" * 64


def make_chunk(chunk_id: str, *, title: str, content: str, domain: str) -> KnowledgeChunk:
    return KnowledgeChunk(
        chunk_id=chunk_id,
        title=title,
        content=content,
        source_type="theory",
        domain=domain,
        tags=[domain],
        metadata={
            "content_sha256": ("a" if chunk_id == "redis_consistency" else "b")
            * 64,
            "corpus_manifest_sha256": MANIFEST_HASH,
        },
        score=0.9,
    )


def make_v2_plan() -> InterviewPlan:
    return InterviewPlan(
        title="Bound plan",
        questions=[
            InterviewQuestion(
                id="q1",
                kind="technical",
                prompt="Explain Redis consistency.",
                focus="Redis",
            ),
            InterviewQuestion(
                id="q2",
                kind="technical",
                prompt="Explain Kafka delivery.",
                focus="Kafka",
            ),
        ],
        prep_context=PrepContext(
            schema_version="v2",
            summary="Grounded",
            knowledge_status="completed",
            topics=[
                PrepKnowledgeTopic(
                    id="topic-redis",
                    label="Redis",
                    source="retrieval",
                    evidence="Redis safe summary",
                    tags=["redis"],
                    evidence_ids=["redis_consistency"],
                ),
                PrepKnowledgeTopic(
                    id="topic-kafka",
                    label="Kafka",
                    source="retrieval",
                    evidence="Kafka safe summary",
                    tags=["kafka"],
                    evidence_ids=["kafka_delivery"],
                ),
            ],
            evidence_refs=[
                KnowledgeEvidenceRef(
                    evidence_id="redis_consistency",
                    title="Redis consistency",
                    domain="redis",
                    source_type="theory",
                    score=0.9,
                    content_sha256="a" * 64,
                    corpus_manifest_sha256=MANIFEST_HASH,
                    candidate_summary="Redis safe summary",
                ),
                KnowledgeEvidenceRef(
                    evidence_id="kafka_delivery",
                    title="Kafka delivery",
                    domain="kafka",
                    source_type="theory",
                    score=0.9,
                    content_sha256="b" * 64,
                    corpus_manifest_sha256=MANIFEST_HASH,
                    candidate_summary="Kafka safe summary",
                ),
            ],
            question_hints=[
                PrepQuestionHint(
                    question_id="q1",
                    topic_ids=["topic-redis"],
                    evidence_ids=["redis_consistency"],
                    follow_up_hints=["Probe Redis failure handling."],
                ),
                PrepQuestionHint(
                    question_id="q2",
                    topic_ids=["topic-kafka"],
                    evidence_ids=["kafka_delivery"],
                    follow_up_hints=["Probe Kafka retry semantics."],
                ),
            ],
            binding_snapshot=KnowledgeBindingSnapshot(
                prep_run_id="prep-1",
                corpus_manifest_sha256=MANIFEST_HASH,
                status="completed",
            ),
        ),
    )


class SearchForbiddenRepository(InMemoryKnowledgeRepository):
    def __init__(self, chunks):
        super().__init__(chunks)
        self.search_calls = 0

    def search(self, *args, **kwargs):
        self.search_calls += 1
        raise AssertionError("v2 binding must not use semantic search")


def make_repository() -> SearchForbiddenRepository:
    return SearchForbiddenRepository(
        [
            make_chunk(
                "redis_consistency",
                title="Redis consistency",
                content="Redis internal consistency evidence.",
                domain="redis",
            ),
            make_chunk(
                "kafka_delivery",
                title="Kafka delivery",
                content="Kafka internal delivery evidence.",
                domain="kafka",
            ),
        ]
    )


def test_v2_resolver_uses_only_current_question_ids_and_never_searches():
    repository = make_repository()
    resolver = KnowledgeBindingResolver(repository)

    resolution = resolver.resolve(make_v2_plan(), "q1")

    assert repository.search_calls == 0
    assert repository.get_by_ids_calls == [
        {"ids": ["redis_consistency"], "expected_hashes": {"redis_consistency": "a" * 64}}
    ]
    assert resolution.retrieval_path == "bound_evidence_ids"
    assert resolution.evidence_ids == ["redis_consistency"]
    evidence_messages = [
        message for message in resolution.messages if message["role"] == "knowledge_evidence"
    ]
    assert len(evidence_messages) == 1
    assert "Redis internal consistency evidence" in evidence_messages[0]["content"]
    assert "Kafka internal delivery evidence" not in str(resolution.messages)


def test_v2_hash_mismatch_degrades_without_using_replaced_content():
    repository = make_repository()
    repository.chunks[0] = repository.chunks[0].model_copy(
        update={"metadata": {**repository.chunks[0].metadata, "content_sha256": "changed"}}
    )
    resolver = KnowledgeBindingResolver(repository)

    resolution = resolver.resolve(make_v2_plan(), "q1")

    assert resolution.retrieval_path == "degraded"
    assert resolution.degraded_reason == "evidence_version_mismatch"
    assert resolution.evidence_ids == []
    assert not any(message["role"] == "knowledge_evidence" for message in resolution.messages)
    assert "Redis internal consistency evidence" not in str(resolution.messages)
    assert repository.search_calls == 0


def test_v2_missing_binding_degrades_without_semantic_search():
    plan = make_v2_plan()
    plan.prep_context.question_hints[0].evidence_ids = []
    repository = make_repository()

    resolution = KnowledgeBindingResolver(repository).resolve(plan, "q1")

    assert resolution.retrieval_path == "degraded"
    assert resolution.degraded_reason == "missing_evidence_binding"
    assert repository.get_by_ids_calls == []
    assert repository.search_calls == 0


def test_v1_plan_keeps_stage41_hint_without_repository_reads():
    plan = make_v2_plan()
    plan.prep_context.schema_version = "v1"
    repository = make_repository()

    resolution = KnowledgeBindingResolver(repository).resolve(plan, "q1")

    assert resolution.retrieval_path == "legacy_prep_hint"
    assert any(message["role"] == "knowledge_agent" for message in resolution.messages)
    assert repository.get_by_ids_calls == []
    assert repository.search_calls == 0


def test_repository_failure_degrades_but_keeps_safe_hint():
    class FailingRepository(SearchForbiddenRepository):
        def get_by_ids(self, ids, *, expected_hashes=None):
            raise RuntimeError("database unavailable")

    resolver = KnowledgeBindingResolver(FailingRepository([]))

    resolution = resolver.resolve(make_v2_plan(), "q1")

    assert resolution.retrieval_path == "degraded"
    assert resolution.degraded_reason == "knowledge_unavailable"
    assert any(message["role"] == "knowledge_agent" for message in resolution.messages)
    assert not any(message["role"] == "knowledge_evidence" for message in resolution.messages)
