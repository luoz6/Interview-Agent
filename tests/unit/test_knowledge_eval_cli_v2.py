import json

from app.ports.runtime import KnowledgeLookupResult
from app.services.knowledge_eval_dataset_v2 import (
    KnowledgeRetrievalCaseV2,
    KnowledgeRetrievalDatasetV2,
)
from app.domain.knowledge.models import KnowledgeChunk
from scripts.evaluate_knowledge_retrieval_v2 import (
    evaluate_knowledge_retrieval_v2,
    write_evaluation_result_v2,
)


class FakeRepository:
    def __init__(self):
        self.embedding_provider = type(
            "Identity",
            (),
            {
                "provider_name": "fake",
                "model_name": "fake-bge",
                "model_revision": "fake-v2",
            },
        )()
        self.chunk = KnowledgeChunk(
            chunk_id="redis_consistency",
            title="Redis 缓存一致性的边界",
            content="不得写入产物的正文",
            source_type="theory",
            domain="redis",
            tags=["redis", "缓存"],
            metadata={
                "content_sha256": "a" * 64,
                "corpus_manifest_sha256": "b" * 64,
                "references": ["https://secret.example/source"],
            },
            score=0.91,
        )

    def search(
        self,
        query_text,
        *,
        job_tags,
        source_types=None,
        domains=None,
        limit=5,
    ):
        assert "缓存" in query_text
        assert domains == ["redis"]
        assert limit == 5
        return [self.chunk]

    def get_by_ids(self, ids, *, expected_hashes=None):
        assert expected_hashes == {"redis_consistency": "a" * 64}
        return KnowledgeLookupResult(found=[self.chunk])

    def warm_embedding(self, text):
        assert text == "知识检索预热"

    def get_active_corpus_version(self):
        return "stage44b1-test"


def make_dataset() -> KnowledgeRetrievalDatasetV2:
    return KnowledgeRetrievalDatasetV2(
        version="pilot-test",
        cases=[
            KnowledgeRetrievalCaseV2(
                case_id="redis-consistency",
                evaluation_group="redis",
                query_text="缓存一致性怎样处理？",
                canonical_tags=["redis"],
                source_types=["theory"],
                allowed_domains=["redis"],
                primary_relevant_chunk_ids=["redis_consistency"],
                excluded_chunk_ids=["redis_distributed_lock"],
            )
        ],
    )


def test_v2_evaluator_searches_binds_and_replays_without_sensitive_output():
    result = evaluate_knowledge_retrieval_v2(
        make_dataset(), FakeRepository(), vector_validity_rate=1.0
    )

    assert result["metrics"]["passed"] is True
    assert result["provider"] == "fake"
    assert result["corpus_version"] == "stage44b1-test"
    assert result["cases"][0] == {
        "case_id": "redis-consistency",
        "status": "completed",
        "retrieved_ids": ["redis_consistency"],
        "scores": {"redis_consistency": 0.91},
        "bound_evidence_ids": ["redis_consistency"],
        "replayed_evidence_ids": ["redis_consistency"],
        "latency_ms": result["cases"][0]["latency_ms"],
    }
    serialized = json.dumps(result, ensure_ascii=False)
    assert "缓存一致性怎样处理" not in serialized
    assert "不得写入产物的正文" not in serialized
    assert "secret.example" not in serialized


def test_v2_writer_uses_a_strict_top_level_allowlist(tmp_path):
    result = evaluate_knowledge_retrieval_v2(
        make_dataset(), FakeRepository(), vector_validity_rate=1.0
    )
    result["query_text"] = "不得写入的查询"
    output = tmp_path / "result.json"

    write_evaluation_result_v2(result, output)

    saved = output.read_text(encoding="utf-8")
    assert "query_text" not in saved
    assert "不得写入的查询" not in saved

