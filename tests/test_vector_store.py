import pytest

from app.services.vector_store import KnowledgeChunk


def test_knowledge_chunk_preserves_metadata():
    chunk = KnowledgeChunk(
        chunk_id="redis-1",
        title="Redis cache consistency",
        content="Delete cache after updating the database.",
        source_type="theory",
        domain="redis",
        tags=["redis", "backend"],
        metadata={"section": "consistency"},
    )

    assert chunk.tags == ["redis", "backend"]
    assert chunk.metadata["section"] == "consistency"


@pytest.mark.pgvector
def test_pgvector_search_signature_smoke():
    from app.services.vector_store import PgVectorKnowledgeStore

    store = PgVectorKnowledgeStore(
        dsn="postgresql://placeholder",
        table_name="knowledge_chunks",
        embedding_model_name="BAAI/bge-m3",
        embedding_dimension=1024,
    )

    assert store.table_name == "knowledge_chunks"
    assert store.embedding_dimension == 1024
