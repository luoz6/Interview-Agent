from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.knowledge_corpus_schema import load_knowledge_document_v2
from app.services.knowledge_ingestion import IngestionSummary, KnowledgeReleaseService
from app.adapters.pgvector.repository import KnowledgeChunk, PgVectorKnowledgeStore, get_knowledge_store
from scripts.build_knowledge_manifest_v2 import (
    DEFAULT_CORPUS_VERSION,
    KNOWLEDGE_V2_ROOT,
    build_manifest_v2,
    content_sha256,
    iter_markdown_files,
    metadata_sha256,
)


def _manifest_by_id(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    if manifest.get("manifest_schema_version") != 2:
        raise ValueError("v2 loader requires manifest_schema_version 2")
    entries = manifest.get("chunks")
    if not isinstance(entries, list):
        raise ValueError("v2 manifest chunks must be a list")
    return {str(entry["chunk_id"]): entry for entry in entries}


def build_chunks_v2(
    knowledge_root: Path | str = KNOWLEDGE_V2_ROOT,
    *,
    manifest: dict[str, Any] | None = None,
) -> list[KnowledgeChunk]:
    root = Path(knowledge_root)
    resolved_manifest = manifest or build_manifest_v2(root)
    entries = _manifest_by_id(resolved_manifest)
    chunks: list[KnowledgeChunk] = []
    include_extensions = resolved_manifest.get("corpus_version") != "stage44b1-zh-v2"
    for path in iter_markdown_files(root, include_extensions=include_extensions):
        document = load_knowledge_document_v2(path)
        metadata = document.metadata
        entry = entries.get(metadata.id)
        if entry is None:
            raise ValueError(f"v2 manifest is missing chunk: {metadata.id}")
        expected_content_hash = content_sha256(document)
        expected_metadata_hash = metadata_sha256(document)
        if entry.get("content_sha256") != expected_content_hash:
            raise ValueError(f"v2 manifest content hash mismatch: {metadata.id}")
        if entry.get("metadata_sha256") != expected_metadata_hash:
            raise ValueError(f"v2 manifest metadata hash mismatch: {metadata.id}")
        relative_path = path.relative_to(root).as_posix()
        if entry.get("source_path") != relative_path:
            raise ValueError(f"v2 manifest source path mismatch: {metadata.id}")

        runtime_metadata = {
            "source_path": relative_path,
            "content_kind": metadata.content_kind,
            "aliases": list(metadata.aliases),
            "technical_terms": list(metadata.technical_terms),
            "topic": metadata.topic,
            "metadata_schema_version": metadata.metadata_schema_version,
            "difficulty": metadata.difficulty,
            "question_patterns": list(metadata.question_patterns),
            "content_sha256": expected_content_hash,
            "metadata_sha256": expected_metadata_hash,
            "corpus_manifest_sha256": resolved_manifest["corpus_manifest_sha256"],
            "corpus_version": resolved_manifest["corpus_version"],
            "authority_metadata": {
                "policy_version": "knowledge-authority-v1",
                "status": "schema_validated",
                "has_official_reference": any(
                    reference.source_kind == "official_cn"
                    for reference in metadata.references
                ),
                "independent_secondary_source_count": len(
                    {
                        (
                            reference.publisher.casefold(),
                            str(reference.url.host or "").casefold(),
                        )
                        for reference in metadata.references
                        if reference.source_kind == "secondary_cn"
                    }
                ),
            },
            "provenance": {
                "source_path": relative_path,
                "metadata_sha256": expected_metadata_hash,
            },
        }
        chunks.append(
            KnowledgeChunk(
                chunk_id=metadata.id,
                title=metadata.title,
                content=document.body,
                source_type=metadata.source_type,
                domain=metadata.domain,
                tags=list(metadata.tags),
                metadata=runtime_metadata,
            )
        )

    if len(chunks) != resolved_manifest.get("chunk_count"):
        raise ValueError("v2 manifest chunk count mismatch")
    if set(entries) != {chunk.chunk_id for chunk in chunks}:
        raise ValueError("v2 manifest chunk IDs mismatch")
    return chunks


def _resolve_store(store: PgVectorKnowledgeStore | None = None) -> PgVectorKnowledgeStore:
    if store is not None:
        return store
    try:
        return get_knowledge_store()
    except KeyError as exc:
        raise RuntimeError("POSTGRES_DSN is required to load knowledge into pgvector") from exc


def load_knowledge_v2(
    *,
    store: PgVectorKnowledgeStore | None = None,
    corpus_version: str,
    knowledge_root: Path | str = KNOWLEDGE_V2_ROOT,
) -> IngestionSummary:
    root = Path(knowledge_root)
    manifest = build_manifest_v2(root, corpus_version=corpus_version)
    chunks = build_chunks_v2(root, manifest=manifest)
    resolved_store = _resolve_store(store)
    ingestor = KnowledgeReleaseService(
        store=resolved_store,
        provider=resolved_store.embedding_provider,
    )
    return ingestor.ingest(chunks=chunks, manifest=manifest)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Load the isolated Stage 44B v2 knowledge corpus")
    parser.add_argument("--corpus-version", required=True)
    parser.add_argument("--knowledge-root", default=str(KNOWLEDGE_V2_ROOT))
    args = parser.parse_args(argv)
    summary = load_knowledge_v2(
        corpus_version=args.corpus_version,
        knowledge_root=args.knowledge_root,
    )
    print(summary.model_dump_json())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
