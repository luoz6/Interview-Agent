from __future__ import annotations

import json

import pytest

from app.ports.runtime import KnowledgeLookupResult
from app.services.knowledge_eval_dataset import load_knowledge_retrieval_dataset
from app.services.knowledge_ingestion import IngestionSummary
from scripts.knowledge_acceptance_stage44a import run_stage44a_acceptance
from scripts.knowledge_acceptance import main
from scripts.build_knowledge_manifest import build_manifest
from scripts.load_knowledge import KNOWLEDGE_ROOT, build_chunks


CORPUS_VERSION = "stage44a-bge-m3-v1"


class FakeProvider:
    provider_name = "siliconflow"
    model_name = "BAAI/bge-m3"
    model_revision = "siliconflow-test-revision"
    dimension = 1024

    def snapshot_metrics(self):
        return {
            "request_count": 2,
            "retry_count": 0,
            "error_counts": {},
            "latency_p50_ms": 10.0,
            "latency_p95_ms": 12.0,
        }


class FakeRepository:
    def __init__(self, dataset, chunks):
        self.embedding_provider = FakeProvider()
        self.by_id = {chunk.chunk_id: chunk for chunk in chunks}
        self.by_query = {case.query_text: case for case in dataset.cases}

    def warm_embedding(self, text):
        return [0.0] * 1024

    def get_active_corpus_version(self):
        return CORPUS_VERSION

    def search(self, query_text, *, job_tags, source_types=None, limit=3):
        case = self.by_query[query_text]
        if case.category == "negative":
            return []
        chunk = self.by_id[case.relevant_chunk_ids[0]]
        return [chunk.model_copy(update={"score": 0.95})]

    def get_by_ids(self, ids, *, expected_hashes=None):
        result = KnowledgeLookupResult()
        for chunk_id in ids:
            chunk = self.by_id.get(chunk_id)
            if chunk is None:
                result.missing.append(chunk_id)
            elif chunk.metadata["content_sha256"] != (expected_hashes or {}).get(
                chunk_id
            ):
                result.version_mismatch.append(chunk_id)
            else:
                result.found.append(chunk)
        return result


class FakeIngestor:
    def __init__(self, chunks):
        self.calls = []
        self.manifest_hash = chunks[0].metadata["corpus_manifest_sha256"]

    def ingest(self, *, chunks, manifest):
        self.calls.append((list(chunks), manifest))
        return IngestionSummary(
            corpus_version=CORPUS_VERSION,
            manifest_sha256=self.manifest_hash,
            discovered=len(chunks),
            reused=0,
            embedded=len(chunks),
            activated=len(chunks),
            provider_name="siliconflow",
            model_name="BAAI/bge-m3",
            model_revision="siliconflow-test-revision",
            dimension=1024,
        )


def make_inputs():
    dataset = load_knowledge_retrieval_dataset()
    manifest = build_manifest(KNOWLEDGE_ROOT, corpus_version=CORPUS_VERSION)
    chunks = build_chunks(KNOWLEDGE_ROOT, manifest=manifest)
    return dataset, chunks


def test_runner_ingests_then_evaluates_and_writes_only_safe_artifacts(tmp_path):
    dataset, chunks = make_inputs()
    repository = FakeRepository(dataset, chunks)
    ingestor = FakeIngestor(chunks)
    run_dir = tmp_path / "stage44a-run"

    metrics = run_stage44a_acceptance(
        repository=repository,
        ingestor=ingestor,
        dataset=dataset,
        chunks=chunks,
        run_id="stage44a-run",
        run_dir=run_dir,
    )

    assert len(chunks) == 25
    assert len(ingestor.calls) == 1
    assert metrics["passed"] is True
    assert metrics["provider_name"] == "siliconflow"
    assert metrics["corpus_version"] == CORPUS_VERSION
    assert metrics["chunk_count"] == 25
    assert metrics["storage_strategy"] == "exact_pgvector_cosine"
    assert metrics["retrieval_metrics"]["hit_rate_at_3"] == 1.0
    assert len(list((run_dir / "retrieval-cases").glob("*.json"))) == 30
    assert {path.name for path in run_dir.iterdir()} == {
        "manifest.json",
        "metrics.json",
        "report.md",
        "retrieval-cases",
    }
    serialized = "\n".join(
        path.read_text(encoding="utf-8")
        for path in run_dir.rglob("*")
        if path.is_file()
    )
    for blocked in (
        "raw_query",
        "query_text",
        "Content redis",
        "postgresql://",
        "authorization",
    ):
        assert blocked not in serialized


def test_runner_rejects_wrong_corpus_count_and_dataset_version_before_ingestion(
    tmp_path,
):
    dataset, chunks = make_inputs()
    ingestor = FakeIngestor(chunks)
    repository = FakeRepository(dataset, chunks)

    with pytest.raises(ValueError, match="25 chunks"):
        run_stage44a_acceptance(
            repository=repository,
            ingestor=ingestor,
            dataset=dataset,
            chunks=chunks[:-1],
            run_id="bad-count",
            run_dir=tmp_path / "bad-count",
        )
    wrong_dataset = dataset.model_copy(update={"version": "wrong-version"})
    with pytest.raises(ValueError, match="dataset version"):
        run_stage44a_acceptance(
            repository=repository,
            ingestor=ingestor,
            dataset=wrong_dataset,
            chunks=chunks,
            run_id="bad-dataset",
            run_dir=tmp_path / "bad-dataset",
        )

    assert ingestor.calls == []


def test_runner_marks_identity_or_degraded_case_as_failed(tmp_path):
    dataset, chunks = make_inputs()
    repository = FakeRepository(dataset, chunks)
    ingestor = FakeIngestor(chunks)
    ingestor.manifest_hash = "different-manifest"

    metrics = run_stage44a_acceptance(
        repository=repository,
        ingestor=ingestor,
        dataset=dataset,
        chunks=chunks,
        run_id="identity-failure",
        run_dir=tmp_path / "identity-failure",
    )

    assert metrics["passed"] is False
    assert "identity_mismatch" in metrics["failure_reasons"]
    saved = json.loads(
        (tmp_path / "identity-failure" / "metrics.json").read_text(
            encoding="utf-8"
        )
    )
    assert saved["passed"] is False


def test_cli_requires_explicit_opt_in_before_building_real_dependencies(
    monkeypatch,
    tmp_path,
):
    args = [
        "stage44a",
        "--run-id",
        "no-network",
        "--run-dir",
        str(tmp_path / "no-network"),
    ]
    monkeypatch.delenv("RUN_SILICONFLOW_ACCEPTANCE", raising=False)
    with pytest.raises(RuntimeError, match="RUN_SILICONFLOW_ACCEPTANCE"):
        main(args)

    monkeypatch.setenv("RUN_SILICONFLOW_ACCEPTANCE", "1")
    monkeypatch.setenv("EMBEDDING_PROVIDER", "disabled")
    with pytest.raises(RuntimeError, match="EMBEDDING_PROVIDER=siliconflow"):
        main(args)
