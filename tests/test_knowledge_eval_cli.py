import json
from pathlib import Path

from app.ports.runtime import KnowledgeLookupResult
from app.services.knowledge_eval_dataset import (
    KnowledgeRetrievalCase,
    KnowledgeRetrievalDataset,
)
from app.services.vector_store import KnowledgeChunk
from scripts.evaluate_knowledge_retrieval import (
    evaluate_knowledge_retrieval,
    write_evaluation_result,
)


class FakeEvaluationRepository:
    def __init__(self) -> None:
        self.search_calls = 0
        self.lookup_calls = 0
        self.chunks = {
            "redis_consistency": KnowledgeChunk(
                chunk_id="redis_consistency",
                title="Redis consistency",
                content="Internal content",
                source_type="theory",
                domain="redis",
                tags=["redis"],
                metadata={
                    "content_sha256": "a" * 64,
                    "corpus_manifest_sha256": "b" * 64,
                },
                score=0.9,
            ),
            "mysql_indexing": KnowledgeChunk(
                chunk_id="mysql_indexing",
                title="MySQL indexing",
                content="Internal content",
                source_type="theory",
                domain="mysql",
                tags=["mysql"],
                metadata={
                    "content_sha256": "c" * 64,
                    "corpus_manifest_sha256": "b" * 64,
                },
                score=0.8,
            ),
        }

    def search(self, query_text, *, job_tags, source_types=None, limit=3):
        self.search_calls += 1
        if "vacation" in query_text:
            return []
        chunk_id = "redis_consistency" if "redis" in query_text else "mysql_indexing"
        return [self.chunks[chunk_id]]

    def get_by_ids(self, ids, *, expected_hashes=None):
        self.lookup_calls += 1
        result = KnowledgeLookupResult()
        for chunk_id in ids:
            chunk = self.chunks.get(chunk_id)
            if chunk is None:
                result.missing.append(chunk_id)
            elif chunk.metadata["content_sha256"] != (expected_hashes or {}).get(chunk_id):
                result.version_mismatch.append(chunk_id)
            else:
                result.found.append(chunk)
        return result


def make_dataset() -> KnowledgeRetrievalDataset:
    return KnowledgeRetrievalDataset(
        version="runner-test",
        cases=[
            KnowledgeRetrievalCase(
                case_id="redis",
                category="relevant",
                domain="redis",
                query_text="redis consistency",
                canonical_tags=["redis"],
                relevant_chunk_ids=["redis_consistency"],
            ),
            KnowledgeRetrievalCase(
                case_id="mysql",
                category="weak_keyword",
                domain="mysql",
                query_text="large mysql index",
                canonical_tags=["mysql"],
                relevant_chunk_ids=["mysql_indexing"],
            ),
            KnowledgeRetrievalCase(
                case_id="negative",
                category="negative",
                domain="redis",
                query_text="vacation approval",
                canonical_tags=["redis"],
            ),
        ],
    )


def test_evaluation_runner_uses_repository_without_llm_and_preserves_hash_continuity():
    repository = FakeEvaluationRepository()

    result = evaluate_knowledge_retrieval(make_dataset(), repository)

    assert repository.search_calls == 3
    assert repository.lookup_calls == 2
    assert result["metrics"]["passed"] is True
    assert result["metrics"]["evidence_continuity_rate"] == 1.0
    assert result["metrics"]["invalid_reference_rate"] == 0.0
    assert result["corpus_manifest_sha256"] == "b" * 64
    assert result["cases"][0]["scores"] == {"redis_consistency": 0.9}


def test_evaluation_artifact_is_json_and_does_not_expose_connection_details(tmp_path):
    output = tmp_path / "knowledge-eval.json"
    result = evaluate_knowledge_retrieval(make_dataset(), FakeEvaluationRepository())
    result["internal_test_dsn"] = "postgresql://secret:secret@localhost/private"

    write_evaluation_result(result, output)

    saved = output.read_text(encoding="utf-8")
    payload = json.loads(saved)
    assert payload["dataset_version"] == "runner-test"
    assert "internal_test_dsn" not in payload
    assert "postgresql://" not in saved
    assert "Internal content" not in saved


def test_evaluation_script_has_no_llm_dependency():
    source = Path("scripts/evaluate_knowledge_retrieval.py").read_text(encoding="utf-8")

    assert "OpenAIInterviewLLM" not in source
    assert "generate_plan" not in source
    assert "generate_report" not in source
