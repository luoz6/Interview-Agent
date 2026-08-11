from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from app.services.embedding_providers import validate_embedding_batch
from app.domain.knowledge.models import KnowledgeChunk


@dataclass(frozen=True)
class PreparedKnowledgeChunk:
    chunk: KnowledgeChunk
    content_sha256: str
    embedding: list[float]


class IngestionSummary(BaseModel):
    corpus_version: str
    manifest_sha256: str
    discovered: int
    reused: int
    embedded: int
    activated: int
    provider_name: str
    model_name: str
    model_revision: str
    dimension: int


class KnowledgeReleaseService:
    def __init__(self, *, store, provider) -> None:
        self.store = store
        self.provider = provider

    def ingest(
        self,
        *,
        chunks: list[KnowledgeChunk],
        manifest: dict[str, Any],
    ) -> IngestionSummary:
        identity_by_id = validate_manifest_and_chunks(manifest, chunks)
        corpus_version = str(manifest["corpus_version"])
        manifest_sha256 = str(manifest["corpus_manifest_sha256"])

        self.store.ensure_schema()
        self.store.migrate_legacy_rows()
        reusable = self.store.find_reusable_embeddings(
            chunks,
            provider_name=self.provider.provider_name,
            model_name=self.provider.model_name,
            model_revision=self.provider.model_revision,
            dimension=self.provider.dimension,
        )
        requested_ids = {chunk.chunk_id for chunk in chunks}
        reusable = {
            chunk_id: vector
            for chunk_id, vector in reusable.items()
            if chunk_id in requested_ids
        }
        if reusable:
            reusable_ids = sorted(reusable)
            reusable_vectors = validate_embedding_batch(
                [reusable[chunk_id] for chunk_id in reusable_ids],
                expected_count=len(reusable_ids),
                dimension=self.provider.dimension,
            )
            reusable = dict(zip(reusable_ids, reusable_vectors, strict=True))

        missing = [chunk for chunk in chunks if chunk.chunk_id not in reusable]
        generated: list[list[float]] = []
        if missing:
            generated = self.provider.embed_documents(
                [f"{chunk.title}\n{chunk.content}" for chunk in missing]
            )
            generated = validate_embedding_batch(
                generated,
                expected_count=len(missing),
                dimension=self.provider.dimension,
            )
        generated_by_id = {
            chunk.chunk_id: vector
            for chunk, vector in zip(missing, generated, strict=True)
        }
        prepared = [
            PreparedKnowledgeChunk(
                chunk=chunk,
                content_sha256=identity_by_id[chunk.chunk_id],
                embedding=(
                    reusable[chunk.chunk_id]
                    if chunk.chunk_id in reusable
                    else generated_by_id[chunk.chunk_id]
                ),
            )
            for chunk in chunks
        ]
        self.store.activate_corpus(
            corpus_version=corpus_version,
            manifest_sha256=manifest_sha256,
            provider=self.provider,
            chunks=prepared,
        )
        return IngestionSummary(
            corpus_version=corpus_version,
            manifest_sha256=manifest_sha256,
            discovered=len(chunks),
            reused=len(reusable),
            embedded=len(missing),
            activated=len(prepared),
            provider_name=self.provider.provider_name,
            model_name=self.provider.model_name,
            model_revision=self.provider.model_revision,
            dimension=self.provider.dimension,
        )


def validate_manifest_and_chunks(
    manifest: dict[str, Any],
    chunks: list[KnowledgeChunk],
) -> dict[str, str]:
    if not chunks:
        raise ValueError("knowledge corpus must contain at least one chunk")
    corpus_version = manifest.get("corpus_version")
    manifest_sha256 = manifest.get("corpus_manifest_sha256")
    if not isinstance(corpus_version, str) or not corpus_version.strip():
        raise ValueError("knowledge manifest requires corpus_version")
    if not isinstance(manifest_sha256, str) or not manifest_sha256.strip():
        raise ValueError("knowledge manifest requires corpus_manifest_sha256")

    chunk_ids = [chunk.chunk_id for chunk in chunks]
    if len(chunk_ids) != len(set(chunk_ids)):
        raise ValueError("knowledge chunks contain duplicate IDs")
    if manifest.get("chunk_count") != len(chunks):
        raise ValueError("knowledge manifest chunk count mismatch")
    manifest_chunks = manifest.get("chunks")
    if not isinstance(manifest_chunks, list):
        raise ValueError("knowledge manifest chunks must be a list")

    identity_by_id: dict[str, str] = {}
    for entry in manifest_chunks:
        if not isinstance(entry, dict):
            raise ValueError("knowledge manifest chunk must be an object")
        chunk_id = entry.get("chunk_id")
        content_sha256 = entry.get("content_sha256")
        if not isinstance(chunk_id, str) or not chunk_id:
            raise ValueError("knowledge manifest chunk requires chunk_id")
        if chunk_id in identity_by_id:
            raise ValueError("knowledge manifest contains duplicate IDs")
        if not isinstance(content_sha256, str) or not content_sha256:
            raise ValueError("knowledge manifest chunk requires content_sha256")
        identity_by_id[chunk_id] = content_sha256

    if set(identity_by_id) != set(chunk_ids):
        raise ValueError("knowledge manifest chunk IDs mismatch")
    for chunk in chunks:
        chunk_hash = chunk.metadata.get("content_sha256")
        if chunk_hash != identity_by_id[chunk.chunk_id]:
            raise ValueError("knowledge manifest content hash mismatch")
    return identity_by_id


KnowledgeCorpusIngestor = KnowledgeReleaseService
