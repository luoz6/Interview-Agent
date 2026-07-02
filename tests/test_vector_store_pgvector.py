import os
import uuid

import pytest

from app.services.vector_store import KnowledgeChunk, PgVectorKnowledgeStore
from tests.test_vector_store import FakeEmbeddingModel


def make_chunk(
    chunk_id: str,
    *,
    title: str,
    content: str,
    source_type: str,
    domain: str,
    tags: list[str],
) -> KnowledgeChunk:
    return KnowledgeChunk(
        chunk_id=chunk_id,
        title=title,
        content=content,
        source_type=source_type,
        domain=domain,
        tags=tags,
        metadata={"source": "pgvector-test"},
    )


@pytest.mark.pgvector
def test_pgvector_roundtrip_filters_by_tag_and_source_type():
    dsn = os.getenv("POSTGRES_DSN")
    if not dsn:
        pytest.skip("POSTGRES_DSN is not configured")

    table_name = f"knowledge_chunks_{uuid.uuid4().hex[:8]}"
    store = PgVectorKnowledgeStore(
        dsn=dsn,
        table_name=table_name,
        embedding_model_name="BAAI/bge-m3",
        embedding_dimension=3,
        embedding_model=FakeEmbeddingModel(),
    )

    redis_chunk = make_chunk(
        "redis-1",
        title="Redis cache consistency",
        content="Delete cache after database writes and handle race conditions.",
        source_type="theory",
        domain="redis",
        tags=["redis", "general"],
    )
    mysql_chunk = make_chunk(
        "mysql-1",
        title="MySQL indexing",
        content="Use covering indexes for read-heavy queries.",
        source_type="theory",
        domain="mysql",
        tags=["mysql", "general"],
    )

    try:
        store.upsert_chunks([redis_chunk, mysql_chunk])
        store.upsert_chunks([redis_chunk])

        results = store.search(
            "Redis cache invalidation",
            job_tags=["redis"],
            source_types=["theory"],
            limit=5,
        )

        assert results
        assert results[0].chunk_id == "redis-1"
        assert results[0].score is not None
    finally:
        psycopg2, _ = PgVectorKnowledgeStore._import_psycopg2()
        with psycopg2.connect(dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(f'DROP TABLE IF EXISTS "{table_name}"')
