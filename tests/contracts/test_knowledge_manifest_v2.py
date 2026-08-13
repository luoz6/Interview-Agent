from pathlib import Path

from scripts.build_knowledge_manifest_v2 import build_manifest_v2


_BODY = "这是中文知识正文，用于描述机制、边界、失败处理和验证方法。" * 12


def _document(chunk_id: str, title: str = "Redis 缓存一致性边界", body: str = _BODY) -> str:
    return f"""---
id: {chunk_id}
title: {title}
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


def test_v2_manifest_is_stable_and_separate_from_v1(tmp_path: Path):
    root = tmp_path / "knowledge_v2"
    second_root = tmp_path / "knowledge_v2_second"
    _write(root, "z/two.md", _document("two", "Kafka 消息可靠性", _BODY + "消息系统正文"))
    _write(root, "a/one.md", _document("one"))
    _write(second_root, "a/one.md", _document("one"))
    _write(
        second_root,
        "z/two.md",
        _document("two", "Kafka 消息可靠性", _BODY + "消息系统正文"),
    )

    manifest = build_manifest_v2(root, corpus_version="stage44b1-test")
    second_manifest = build_manifest_v2(
        second_root,
        corpus_version="stage44b1-test",
    )

    assert manifest == second_manifest
    assert manifest["manifest_schema_version"] == 2
    assert manifest["chunk_count"] == 2
    assert manifest["corpus_version"] == "stage44b1-test"
    assert [item["chunk_id"] for item in manifest["chunks"]] == ["one", "two"]
    assert all("content_sha256" in item for item in manifest["chunks"])
    assert all("metadata_sha256" in item for item in manifest["chunks"])
    assert all(
        item["metadata_schema_version"] == "knowledge-metadata-v2.1"
        for item in manifest["chunks"]
    )
    assert all(item["topic"] == "cache-consistency" for item in manifest["chunks"])
    assert all(item["technical_terms"] == ["cache-aside"] for item in manifest["chunks"])
    assert all("references" not in item for item in manifest["chunks"])
    assert all("https://" not in str(item) for item in manifest["chunks"])
    assert manifest["coverage"]["schema_version"] == "knowledge-coverage-v1"
    assert manifest["coverage"]["minimum_evidence_classes"] == [
        "positive",
        "negative",
        "boundary",
    ]
    assert all(
        all(count > 0 for count in counts.values())
        for counts in manifest["coverage"]["evidence_class_counts"].values()
    )


def test_v2_content_hash_covers_embedding_title_and_body(tmp_path: Path):
    root = tmp_path / "knowledge_v2"
    path = root / "redis/item.md"
    _write(root, "redis/item.md", _document("item"))
    first = build_manifest_v2(root)

    path.write_text(_document("item", "新的中文标题"), encoding="utf-8")
    second = build_manifest_v2(root)

    assert first["chunks"][0]["content_sha256"] != second["chunks"][0]["content_sha256"]


def test_v2_manifest_rejects_duplicate_ids_and_content(tmp_path: Path):
    root = tmp_path / "knowledge_v2"
    _write(root, "a/one.md", _document("same"))
    _write(root, "b/two.md", _document("same", "另一个标题"))
    try:
        build_manifest_v2(root)
    except ValueError as exc:
        assert "duplicate" in str(exc)
    else:
        raise AssertionError("duplicate v2 IDs must be rejected")


def test_historical_stage44_manifest_excludes_versioned_extensions(
    tmp_path: Path,
):
    root = tmp_path / "knowledge_v2"
    _write(root, "base/one.md", _document("one"))
    _write(
        root,
        "extensions/memory_p1/two.md",
        _document("two", "扩展中文标题", _BODY + "扩展正文"),
    )

    historical = build_manifest_v2(
        root,
        corpus_version="stage44b1-zh-v2",
    )
    current = build_manifest_v2(
        root,
        corpus_version="memory-p1-zh-v4",
    )

    assert [item["chunk_id"] for item in historical["chunks"]] == ["one"]
    assert [item["chunk_id"] for item in current["chunks"]] == ["one", "two"]
