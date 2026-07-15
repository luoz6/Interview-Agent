import os

import pytest

from app.services.config import DEFAULT_POSTGRES_DSN
from app.services.vector_store import KnowledgeChunk, PgVectorKnowledgeStore


class FakeEmbeddingModel:
    def encode(self, text: str, normalize_embeddings: bool = True):
        base = 0.1 if "redis" in text.lower() else 0.2
        return [base, base + 0.1, base + 0.2]


def make_store() -> PgVectorKnowledgeStore:
    return PgVectorKnowledgeStore(
        dsn="postgresql://placeholder",
        table_name="knowledge_chunks",
        embedding_model_name="BAAI/bge-m3",
        embedding_dimension=3,
        embedding_model=FakeEmbeddingModel(),
        minimum_score=0.35,
    )


def make_chunk() -> KnowledgeChunk:
    return KnowledgeChunk(
        chunk_id="redis-1",
        title="Redis cache consistency",
        content="Delete cache after updating the database.",
        source_type="theory",
        domain="redis",
        tags=["redis", "backend"],
        metadata={"section": "consistency"},
    )


def test_knowledge_chunk_preserves_metadata():
    chunk = make_chunk()

    assert chunk.tags == ["redis", "backend"]
    assert chunk.metadata["section"] == "consistency"


def test_embed_text_uses_injected_model_and_validates_dimension():
    store = make_store()

    vector = store.embed_text("redis cache consistency")

    assert vector == pytest.approx([0.1, 0.2, 0.3])


def test_vector_literal_format_is_pgvector_compatible():
    literal = PgVectorKnowledgeStore._to_vector_literal([0.1, 0.2, 0.3])

    assert literal == "[0.10000000,0.20000000,0.30000000]"


def test_from_env_defaults_to_local_postgres(monkeypatch):
    monkeypatch.delenv("POSTGRES_DSN", raising=False)
    monkeypatch.delenv("PGVECTOR_TABLE", raising=False)

    store = PgVectorKnowledgeStore.from_env()

    assert store.dsn == DEFAULT_POSTGRES_DSN
    assert store.table_name == "knowledge_chunks"
    assert store.minimum_score == 0.35


def test_repository_errors_do_not_expose_dsn_credentials():
    dsn = "postgresql://secret-user:secret-pass@127.0.0.1:1/private-db"
    store = PgVectorKnowledgeStore(
        dsn=dsn,
        table_name="knowledge_chunks",
        embedding_model_name="unused",
        embedding_dimension=3,
        embedding_model=FakeEmbeddingModel(),
    )

    with pytest.raises(RuntimeError) as exc:
        store.get_by_ids(["redis-1"])

    message = str(exc.value)
    assert "secret-user" not in message
    assert "secret-pass" not in message
    assert "private-db" not in message


@pytest.mark.pgvector
def test_pgvector_upsert_and_search_roundtrip():
    dsn = os.getenv("POSTGRES_DSN")
    if not dsn:
        pytest.skip("POSTGRES_DSN is not configured")

    table_name = "knowledge_chunks_test"
    store = PgVectorKnowledgeStore(
        dsn=dsn,
        table_name=table_name,
        embedding_model_name="BAAI/bge-m3",
        embedding_dimension=3,
        embedding_model=FakeEmbeddingModel(),
    )
    chunk = make_chunk()
    store.upsert_chunks([chunk])

    results = store.search(
        "Redis cache invalidation",
        job_tags=["redis"],
        source_types=["theory"],
        limit=3,
    )

    assert results
    assert results[0].chunk_id == "redis-1"
    assert results[0].score is not None
