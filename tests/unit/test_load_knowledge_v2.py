from pathlib import Path

from scripts.build_knowledge_manifest_v2 import build_manifest_v2
from scripts.load_knowledge_v2 import build_chunks_v2


_BODY = "这是中文知识正文，用于描述机制、边界、失败处理和验证方法。" * 12


def _document(chunk_id: str, body: str = _BODY) -> str:
    return f"""---
id: {chunk_id}
title: 中文缓存一致性
domain: redis
source_type: theory
content_kind: mechanism
tags: [redis, 缓存]
aliases: [缓存一致性]
technical_terms: [cache-aside]
topic: cache-consistency
difficulty: intermediate
question_patterns:
  - 缓存和数据库怎样保持一致
  - 缓存更新失败如何补偿
references:
  - title: Redis 中文官方资料
    url: https://redis.io/docs/latest/develop/
    source_kind: official_cn
    publisher: Redis 中文文档
---
{body}
"""


def _write(root: Path, name: str, text: str) -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_v2_loader_never_scans_v1_root(tmp_path: Path):
    v1_root = tmp_path / "knowledge"
    v2_root = tmp_path / "knowledge_v2"
    _write(v1_root, "same.md", _document("same", body="English body"))
    _write(v2_root, "same.md", _document("same"))

    manifest = build_manifest_v2(v2_root)
    chunks = build_chunks_v2(v2_root, manifest=manifest)

    assert len(chunks) == 1
    assert "中文知识正文" in chunks[0].content
    assert "English body" not in chunks[0].content
    assert "references" not in chunks[0].metadata
    assert "https://" not in chunks[0].model_dump_json()


def test_v2_loader_runtime_metadata_contains_safe_manifest_identity(tmp_path: Path):
    root = tmp_path / "knowledge_v2"
    _write(root, "redis/item.md", _document("item"))
    manifest = build_manifest_v2(root, corpus_version="stage44b1-test")

    chunks = build_chunks_v2(root, manifest=manifest)
    chunk = chunks[0]

    assert chunk.metadata["corpus_version"] == "stage44b1-test"
    assert chunk.metadata["corpus_manifest_sha256"] == manifest["corpus_manifest_sha256"]
    assert chunk.metadata["content_sha256"] == manifest["chunks"][0]["content_sha256"]
    assert chunk.metadata["metadata_sha256"] == manifest["chunks"][0]["metadata_sha256"]
    assert chunk.metadata["aliases"] == ["缓存一致性"]
    assert chunk.metadata["technical_terms"] == ["cache-aside"]
    assert chunk.metadata["topic"] == "cache-consistency"
    assert chunk.metadata["metadata_schema_version"] == "knowledge-metadata-v2.1"
    assert chunk.metadata["question_patterns"]
    assert set(chunk.metadata) == {
        "source_path",
        "content_kind",
        "aliases",
        "technical_terms",
        "topic",
        "metadata_schema_version",
        "difficulty",
        "question_patterns",
        "content_sha256",
        "metadata_sha256",
        "corpus_manifest_sha256",
        "corpus_version",
        "authority_metadata",
        "provenance",
    }
    assert chunk.metadata["authority_metadata"] == {
        "policy_version": "knowledge-authority-v1",
        "status": "schema_validated",
        "has_official_reference": True,
        "independent_secondary_source_count": 0,
    }
    assert chunk.metadata["provenance"] == {
        "source_path": "redis/item.md",
        "metadata_sha256": manifest["chunks"][0]["metadata_sha256"],
    }
    assert "references" not in chunk.metadata
    assert all("url" not in key.casefold() for key in chunk.metadata)


def test_v2_loader_rejects_manifest_identity_mismatch(tmp_path: Path):
    root = tmp_path / "knowledge_v2"
    _write(root, "redis/item.md", _document("item"))
    manifest = build_manifest_v2(root)
    manifest["chunks"][0]["content_sha256"] = "0" * 64

    try:
        build_chunks_v2(root, manifest=manifest)
    except ValueError as exc:
        assert "hash" in str(exc)
    else:
        raise AssertionError("manifest/content mismatch must be rejected")
