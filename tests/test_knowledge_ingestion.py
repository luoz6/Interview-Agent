import json

import pytest

from app.services.embedding_providers import EmbeddingProviderError
from app.services.knowledge_ingestion import KnowledgeCorpusIngestor
from app.services.vector_store import KnowledgeChunk


class FakeEmbeddingProvider:
    provider_name = "fake"
    model_name = "fake-bge-m3"
    model_revision = "fake-v1"
    dimension = 3

    def __init__(self, *, vectors=None, error=None):
        self.vectors = vectors
        self.error = error
        self.document_calls = []

    def embed_query(self, text):
        return [0.1, 0.2, 0.3]

    def embed_documents(self, texts):
        self.document_calls.append(list(texts))
        if self.error is not None:
            raise self.error
        if self.vectors is not None:
            return self.vectors
        return [[0.1, 0.2, 0.3] for _ in texts]


class FakeCorpusStore:
    def __init__(self, *, reusable=None, activation_error=None):
        self.reusable = dict(reusable or {})
        self.activation_error = activation_error
        self.ensure_calls = 0
        self.migrate_calls = 0
        self.find_calls = []
        self.activate_calls = []

    def ensure_schema(self):
        self.ensure_calls += 1

    def migrate_legacy_rows(self):
        self.migrate_calls += 1
        return 0

    def find_reusable_embeddings(self, chunks, **identity):
        self.find_calls.append((list(chunks), identity))
        return {
            chunk.chunk_id: self.reusable[chunk.chunk_id]
            for chunk in chunks
            if chunk.chunk_id in self.reusable
        }

    def activate_corpus(self, **payload):
        self.activate_calls.append(payload)
        if self.activation_error is not None:
            raise self.activation_error
        for prepared in payload["chunks"]:
            self.reusable[prepared.chunk.chunk_id] = prepared.embedding


def make_chunk(chunk_id="redis-1", content_hash=None):
    return KnowledgeChunk(
        chunk_id=chunk_id,
        title=f"Title {chunk_id}",
        content=f"Content {chunk_id}",
        source_type="theory",
        domain="redis",
        tags=["redis"],
        metadata={"content_sha256": content_hash or chunk_id.replace("-", "") * 8},
    )


def make_manifest(chunks, *, version="stage44a-bge-m3-v1"):
    return {
        "corpus_version": version,
        "corpus_manifest_sha256": "a" * 64,
        "chunk_count": len(chunks),
        "chunks": [
            {
                "chunk_id": chunk.chunk_id,
                "content_sha256": chunk.metadata["content_sha256"],
            }
            for chunk in chunks
        ],
    }


@pytest.mark.parametrize(
    "chunks,manifest_mutation",
    [
        ([], lambda manifest: None),
        ([make_chunk()], lambda manifest: manifest.update(chunk_count=2)),
        (
            [make_chunk()],
            lambda manifest: manifest["chunks"][0].update(chunk_id="other"),
        ),
        (
            [make_chunk()],
            lambda manifest: manifest["chunks"][0].update(content_sha256="b" * 64),
        ),
        (
            [make_chunk(), make_chunk()],
            lambda manifest: None,
        ),
    ],
)
def test_invalid_manifest_fails_before_store_or_provider_calls(chunks, manifest_mutation):
    manifest = make_manifest(chunks)
    manifest_mutation(manifest)
    store = FakeCorpusStore()
    provider = FakeEmbeddingProvider()

    with pytest.raises(ValueError):
        KnowledgeCorpusIngestor(store=store, provider=provider).ingest(
            chunks=chunks,
            manifest=manifest,
        )

    assert store.ensure_calls == 0
    assert store.find_calls == []
    assert store.activate_calls == []
    assert provider.document_calls == []


def test_only_missing_vectors_are_embedded_and_identity_is_explicit():
    chunks = [make_chunk("redis-1"), make_chunk("redis-2")]
    store = FakeCorpusStore(reusable={"redis-1": [0.9, 0.8, 0.7]})
    provider = FakeEmbeddingProvider(vectors=[[0.1, 0.2, 0.3]])

    summary = KnowledgeCorpusIngestor(store=store, provider=provider).ingest(
        chunks=chunks,
        manifest=make_manifest(chunks),
    )

    assert provider.document_calls == [["Title redis-2\nContent redis-2"]]
    assert store.find_calls[0][1] == {
        "provider_name": "fake",
        "model_name": "fake-bge-m3",
        "model_revision": "fake-v1",
        "dimension": 3,
    }
    prepared = store.activate_calls[0]["chunks"]
    assert [item.embedding for item in prepared] == [
        [0.9, 0.8, 0.7],
        [0.1, 0.2, 0.3],
    ]
    assert summary.reused == 1
    assert summary.embedded == 1


@pytest.mark.parametrize(
    "vectors",
    [
        [],
        [[0.1, 0.2]],
        [[0.1, float("nan"), 0.3]],
    ],
)
def test_invalid_remote_vectors_fail_before_activation(vectors):
    chunks = [make_chunk()]
    store = FakeCorpusStore()
    provider = FakeEmbeddingProvider(vectors=vectors)

    with pytest.raises(ValueError):
        KnowledgeCorpusIngestor(store=store, provider=provider).ingest(
            chunks=chunks,
            manifest=make_manifest(chunks),
        )

    assert store.activate_calls == []


def test_provider_failure_never_opens_activation():
    chunks = [make_chunk()]
    store = FakeCorpusStore()
    provider = FakeEmbeddingProvider(
        error=EmbeddingProviderError("http_503", retryable=True)
    )

    with pytest.raises(EmbeddingProviderError):
        KnowledgeCorpusIngestor(store=store, provider=provider).ingest(
            chunks=chunks,
            manifest=make_manifest(chunks),
        )

    assert store.activate_calls == []


def test_activation_failure_returns_no_summary():
    chunks = [make_chunk()]
    store = FakeCorpusStore(activation_error=RuntimeError("database unavailable"))

    with pytest.raises(RuntimeError, match="database unavailable"):
        KnowledgeCorpusIngestor(
            store=store,
            provider=FakeEmbeddingProvider(),
        ).ingest(chunks=chunks, manifest=make_manifest(chunks))


def test_repeated_identical_release_embeds_documents_only_once():
    chunks = [make_chunk()]
    store = FakeCorpusStore()
    provider = FakeEmbeddingProvider()
    ingestor = KnowledgeCorpusIngestor(store=store, provider=provider)

    first = ingestor.ingest(chunks=chunks, manifest=make_manifest(chunks))
    second = ingestor.ingest(chunks=chunks, manifest=make_manifest(chunks))

    assert len(provider.document_calls) == 1
    assert first.embedded == 1
    assert second.embedded == 0
    assert second.reused == 1
    assert len(store.activate_calls) == 2


def test_summary_is_sanitized_and_contains_only_identity_and_counts():
    chunks = [make_chunk()]
    summary = KnowledgeCorpusIngestor(
        store=FakeCorpusStore(),
        provider=FakeEmbeddingProvider(),
    ).ingest(chunks=chunks, manifest=make_manifest(chunks))

    payload = summary.model_dump()
    assert set(payload) == {
        "corpus_version",
        "manifest_sha256",
        "discovered",
        "reused",
        "embedded",
        "activated",
        "provider_name",
        "model_name",
        "model_revision",
        "dimension",
    }
    serialized = json.dumps(payload)
    for blocked in ("Content redis-1", "postgresql://", "SILICONFLOW_API_KEY", "query"):
        assert blocked not in serialized
