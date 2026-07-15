import json
from pathlib import Path

import pytest

from scripts.build_knowledge_manifest import (
    CORPUS_VERSION,
    build_manifest,
    write_manifest,
)
from scripts.load_knowledge import build_chunks


def _write_chunk(
    root: Path,
    relative_path: str,
    *,
    chunk_id: str,
    content: str,
    domain: str = "redis",
    content_kind: str = "mechanism",
) -> None:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                "---",
                f"id: {chunk_id}",
                f"domain: {domain}",
                "source_type: theory",
                f"content_kind: {content_kind}",
                f"tags: [{domain}]",
                f"title: {chunk_id}",
                "---",
                "",
                content,
                "",
            ]
        ),
        encoding="utf-8",
    )


def test_manifest_is_stable_across_file_creation_order(tmp_path):
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    _write_chunk(first_root, "z/two.md", chunk_id="two", content="Second body.")
    _write_chunk(first_root, "a/one.md", chunk_id="one", content="First body.")
    _write_chunk(second_root, "a/one.md", chunk_id="one", content="First body.")
    _write_chunk(second_root, "z/two.md", chunk_id="two", content="Second body.")

    first = build_manifest(first_root)
    second = build_manifest(second_root)

    assert first == second
    assert [item["chunk_id"] for item in first["chunks"]] == ["one", "two"]


def test_content_change_for_same_logical_id_changes_chunk_and_corpus_hash(tmp_path):
    root = tmp_path / "knowledge"
    _write_chunk(root, "theory/item.md", chunk_id="stable-id", content="Version one.")
    first = build_manifest(root)

    _write_chunk(root, "theory/item.md", chunk_id="stable-id", content="Version two.")
    second = build_manifest(root)

    assert first["chunks"][0]["chunk_id"] == second["chunks"][0]["chunk_id"]
    assert first["chunks"][0]["content_sha256"] != second["chunks"][0]["content_sha256"]
    assert first["corpus_manifest_sha256"] != second["corpus_manifest_sha256"]


def test_duplicate_normalized_content_is_rejected(tmp_path):
    root = tmp_path / "knowledge"
    _write_chunk(root, "a/one.md", chunk_id="one", content="Same meaningful body.")
    _write_chunk(
        root,
        "b/two.md",
        chunk_id="two",
        content="  Same   meaningful\nbody. ",
    )

    with pytest.raises(ValueError, match="duplicate knowledge content"):
        build_manifest(root)


def test_duplicate_logical_id_is_rejected(tmp_path):
    root = tmp_path / "knowledge"
    _write_chunk(root, "a/one.md", chunk_id="same-id", content="First body.")
    _write_chunk(root, "b/two.md", chunk_id="same-id", content="Second body.")

    with pytest.raises(ValueError, match="duplicate knowledge chunk id"):
        build_manifest(root)


def test_repository_corpus_has_25_distinct_reviewable_chunks():
    manifest = build_manifest()

    assert manifest["corpus_version"] == CORPUS_VERSION
    assert manifest["chunk_count"] >= 25
    assert len({item["content_sha256"] for item in manifest["chunks"]}) == manifest[
        "chunk_count"
    ]
    required_kinds = {
        "mechanism",
        "failure_mode",
        "engineering_practice",
        "benchmark",
    }
    for domain in ("redis", "fastapi", "mysql", "kafka", "system-design"):
        domain_chunks = [item for item in manifest["chunks"] if item["domain"] == domain]
        assert len(domain_chunks) >= 5
        assert required_kinds.issubset(
            {item["content_kind"] for item in domain_chunks}
        )


def test_loader_metadata_matches_committed_manifest():
    manifest = build_manifest()
    by_id = {item["chunk_id"]: item for item in manifest["chunks"]}
    chunks = build_chunks()

    assert len(chunks) == manifest["chunk_count"]
    for chunk in chunks:
        expected = by_id[chunk.chunk_id]
        assert chunk.metadata["content_sha256"] == expected["content_sha256"]
        assert chunk.metadata["corpus_manifest_sha256"] == manifest[
            "corpus_manifest_sha256"
        ]
        assert chunk.metadata["corpus_version"] == CORPUS_VERSION


def test_committed_manifest_is_reproducible(tmp_path):
    output = tmp_path / "manifest.json"
    generated = write_manifest(output_path=output)

    assert json.loads(output.read_text(encoding="utf-8")) == generated
    committed = json.loads(
        Path("app/data/knowledge/manifest.json").read_text(encoding="utf-8")
    )
    assert committed == generated
