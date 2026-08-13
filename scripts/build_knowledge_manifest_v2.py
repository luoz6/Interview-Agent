from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.knowledge_corpus_schema import KnowledgeDocumentV2, load_knowledge_document_v2


KNOWLEDGE_V2_ROOT = Path("app/data/knowledge_v2")
DEFAULT_OUTPUT_PATH = KNOWLEDGE_V2_ROOT / "manifest.json"
DEFAULT_CORPUS_VERSION = "memory-p1-zh-v4"
_COVERAGE_TAGS = {
    "python",
    "fastapi",
    "redis",
    "mysql",
    "postgresql",
    "rocketmq",
    "system-design",
    "reliability",
}
_POSITIVE_KINDS = {"mechanism", "engineering_practice", "benchmark"}
_NEGATIVE_KINDS = {"failure_mode", "hard_negative"}
_BOUNDARY_KINDS = {"hard_negative", "engineering_practice"}


def iter_markdown_files(
    knowledge_root: Path | str = KNOWLEDGE_V2_ROOT,
    *,
    include_extensions: bool = True,
) -> list[Path]:
    """Return only documents below the explicitly selected v2 root."""
    root = Path(knowledge_root)
    paths = sorted(root.rglob("*.md"))
    if include_extensions:
        return paths
    return [
        path
        for path in paths
        if "extensions" not in path.relative_to(root).parts
    ]


def _normalized_text(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n").strip()


def _sha256_payload(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def content_sha256(document: KnowledgeDocumentV2) -> str:
    # This is exactly the text sent to the embedding provider by ingestion.
    payload = f"{document.metadata.title}\n{_normalized_text(document.body)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def metadata_sha256(document: KnowledgeDocumentV2) -> str:
    return _sha256_payload(document.metadata.model_dump(mode="json"))


def references_sha256(document: KnowledgeDocumentV2) -> str:
    references = [reference.model_dump(mode="json") for reference in document.metadata.references]
    return _sha256_payload(references)


def build_manifest_v2(
    knowledge_root: Path | str = KNOWLEDGE_V2_ROOT,
    *,
    corpus_version: str = DEFAULT_CORPUS_VERSION,
) -> dict[str, Any]:
    root = Path(knowledge_root)
    if not corpus_version.strip():
        raise ValueError("corpus_version must not be empty")

    entries: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_body_hashes: dict[str, str] = {}
    include_extensions = corpus_version != "stage44b1-zh-v2"
    for path in iter_markdown_files(
        root,
        include_extensions=include_extensions,
    ):
        document = load_knowledge_document_v2(path)
        metadata = document.metadata
        chunk_id = metadata.id
        if chunk_id in seen_ids:
            raise ValueError(f"duplicate v2 knowledge chunk id: {chunk_id}")
        seen_ids.add(chunk_id)

        body_hash = hashlib.sha256(
            _normalized_text(document.body).casefold().encode("utf-8")
        ).hexdigest()
        if body_hash in seen_body_hashes:
            raise ValueError(
                "duplicate v2 knowledge content: "
                f"{seen_body_hashes[body_hash]} and {chunk_id}"
            )
        seen_body_hashes[body_hash] = chunk_id

        entries.append(
            {
                "chunk_id": chunk_id,
                "title": metadata.title,
                "domain": metadata.domain,
                "source_type": metadata.source_type,
                "content_kind": metadata.content_kind,
                "tags": metadata.tags,
                "aliases": metadata.aliases,
                "technical_terms": metadata.technical_terms,
                "topic": metadata.topic,
                "metadata_schema_version": metadata.metadata_schema_version,
                "difficulty": metadata.difficulty,
                "question_patterns": metadata.question_patterns,
                "source_path": path.relative_to(root).as_posix(),
                "content_sha256": content_sha256(document),
                "metadata_sha256": metadata_sha256(document),
                "reference_count": len(metadata.references),
                "references_sha256": references_sha256(document),
            }
        )

    entries.sort(key=lambda item: item["chunk_id"])
    coverage_counts: dict[str, dict[str, int]] = {}
    for entry in entries:
        tags = set(entry["tags"]) | {entry["domain"]}
        for tag in sorted(tags.intersection(_COVERAGE_TAGS)):
            counts = coverage_counts.setdefault(
                tag,
                {"positive": 0, "negative": 0, "boundary": 0},
            )
            kind = entry["content_kind"]
            counts["positive"] += int(kind in _POSITIVE_KINDS)
            counts["negative"] += int(kind in _NEGATIVE_KINDS)
            counts["boundary"] += int(kind in _BOUNDARY_KINDS)
    covered_tags = sorted(
        tag
        for tag, counts in coverage_counts.items()
        if all(counts[evidence_class] > 0 for evidence_class in counts)
    )
    coverage = {
        "schema_version": "knowledge-coverage-v1",
        "canonical_tags": covered_tags,
        "supported_role_groups": ["backend"],
        "minimum_evidence_classes": ["positive", "negative", "boundary"],
        "evidence_class_counts": {
            tag: coverage_counts[tag] for tag in covered_tags
        },
    }
    payload = {
        "corpus_version": corpus_version,
        "chunk_count": len(entries),
        "chunks": entries,
        "coverage": coverage,
    }
    corpus_hash = _sha256_payload(payload)
    return {
        "manifest_schema_version": 2,
        **payload,
        "corpus_manifest_sha256": corpus_hash,
    }


def write_manifest_v2(
    *,
    output_path: Path | str = DEFAULT_OUTPUT_PATH,
    knowledge_root: Path | str = KNOWLEDGE_V2_ROOT,
    corpus_version: str = DEFAULT_CORPUS_VERSION,
) -> dict[str, Any]:
    manifest = build_manifest_v2(knowledge_root, corpus_version=corpus_version)
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build an isolated Stage 44B v2 knowledge manifest")
    parser.add_argument("--knowledge-root", default=str(KNOWLEDGE_V2_ROOT))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT_PATH))
    parser.add_argument("--corpus-version", default=DEFAULT_CORPUS_VERSION)
    args = parser.parse_args(argv)
    manifest = write_manifest_v2(
        output_path=args.output,
        knowledge_root=args.knowledge_root,
        corpus_version=args.corpus_version,
    )
    print(
        json.dumps(
            {
                "corpus_version": manifest["corpus_version"],
                "chunk_count": manifest["chunk_count"],
                "corpus_manifest_sha256": manifest["corpus_manifest_sha256"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
