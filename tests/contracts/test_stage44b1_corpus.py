import json
from collections import Counter
from pathlib import Path

from app.services.knowledge_eval_dataset_v2 import (
    load_knowledge_retrieval_dataset_v2,
)
from scripts.build_knowledge_manifest_v2 import build_manifest_v2
from scripts.load_knowledge_v2 import build_chunks_v2


V1_MANIFEST_PATH = Path("app/data/knowledge/manifest.json")
V2_MANIFEST_PATH = Path("app/data/knowledge_v2/manifest.json")
PILOT_PATH = Path("tests/golden/knowledge_retrieval_v2_pilot.json")


def test_stage44b1_corpus_preserves_shape_while_replacing_kafka_with_rocketmq():
    v1 = json.loads(V1_MANIFEST_PATH.read_text(encoding="utf-8"))
    v2 = build_manifest_v2(corpus_version="stage44b1-zh-v2")

    v1_hashes = {item["chunk_id"]: item["content_sha256"] for item in v1["chunks"]}
    v2_hashes = {item["chunk_id"]: item["content_sha256"] for item in v2["chunks"]}
    assert v2["manifest_schema_version"] == 2
    assert v2["chunk_count"] == 25
    unchanged_ids = set(v1_hashes) & set(v2_hashes)
    assert len(unchanged_ids) == 20
    assert all(v2_hashes[chunk_id] != v1_hashes[chunk_id] for chunk_id in unchanged_ids)
    assert {chunk_id for chunk_id in v1_hashes if chunk_id.startswith("kafka_")}
    assert {chunk_id for chunk_id in v2_hashes if chunk_id.startswith("rocketmq_")}


def test_stage44b1_runtime_metadata_excludes_references_and_urls():
    chunks = build_chunks_v2()
    serialized = "\n".join(chunk.model_dump_json() for chunk in chunks)

    assert "references" not in serialized
    assert "https://" not in serialized
    assert all(chunk.metadata["aliases"] for chunk in chunks)
    assert all(chunk.metadata["topic"] for chunk in chunks)
    assert all(
        chunk.metadata["metadata_schema_version"] == "knowledge-metadata-v2.1"
        for chunk in chunks
    )
    by_id = {chunk.chunk_id: chunk for chunk in chunks}
    assert "owner token" in by_id["redis_distributed_lock"].metadata["technical_terms"]
    assert "message-id" in by_id["rocketmq_delivery"].metadata["technical_terms"]
    assert "covering-index" in by_id["mysql_indexing"].metadata["technical_terms"]
    assert all(chunk.metadata["question_patterns"] for chunk in chunks)


def test_frozen_v1_manifest_remains_reproducible():
    committed = json.loads(V1_MANIFEST_PATH.read_text(encoding="utf-8"))

    assert committed["chunk_count"] == 25
    assert (
        committed["corpus_manifest_sha256"]
        == "ad0948a6dc15af835247eb95b8fad4a069bece865779060e19c708f829eb9320"
    )


def test_stage44b1_distribution_and_pilot_contracts():
    manifest = build_manifest_v2(corpus_version="stage44b1-zh-v2")
    chunks = manifest["chunks"]

    assert Counter(item["domain"] for item in chunks) == {
        "fastapi": 5,
        "redis": 5,
        "mysql": 5,
        "rocketmq": 5,
        "system-design": 5,
    }
    assert Counter(item["content_kind"] for item in chunks) == {
        "benchmark": 5,
        "engineering_practice": 5,
        "failure_mode": 5,
        "hard_negative": 5,
        "mechanism": 5,
    }
    assert Counter(item["difficulty"] for item in chunks) == {
        "advanced": 5,
        "intermediate": 15,
        "beginner": 5,
    }
    assert sum("reliability" in item["tags"] for item in chunks) >= 3

    dataset = load_knowledge_retrieval_dataset_v2(
        PILOT_PATH,
        expected_case_count=12,
        manifest=manifest,
    )
    assert len(dataset.cases) == 12


def test_committed_v2_manifest_matches_rebuilt_payload():
    committed = json.loads(V2_MANIFEST_PATH.read_text(encoding="utf-8"))

    assert committed == build_manifest_v2(corpus_version="memory-p1-zh-v4")
    assert committed["chunk_count"] == 31
    assert "postgresql" in committed["coverage"]["canonical_tags"]
