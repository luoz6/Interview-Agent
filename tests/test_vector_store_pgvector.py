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
    content_sha256: str | None = None,
) -> KnowledgeChunk:
    return KnowledgeChunk(
        chunk_id=chunk_id,
        title=title,
        content=content,
        source_type=source_type,
        domain=domain,
        tags=tags,
        metadata={
            "source": "pgvector-test",
            **(
                {"content_sha256": content_sha256}
                if content_sha256 is not None
                else {}
            ),
        },
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


@pytest.mark.pgvector
def test_get_by_ids_preserves_order_dedupes_and_reports_version_status():
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
    redis = make_chunk(
        "redis-1",
        title="Redis consistency",
        content="Delete cache after the database commit.",
        source_type="theory",
        domain="redis",
        tags=["redis"],
        content_sha256="a" * 64,
    )
    mysql = make_chunk(
        "mysql-1",
        title="MySQL indexing",
        content="Inspect the query plan and scanned rows.",
        source_type="theory",
        domain="mysql",
        tags=["mysql"],
        content_sha256="b" * 64,
    )

    try:
        store.upsert_chunks([redis, mysql])

        result = store.get_by_ids(
            ["mysql-1", "missing", "redis-1", "mysql-1"],
            expected_hashes={
                "mysql-1": "b" * 64,
                "redis-1": "changed-content-hash",
            },
        )

        assert [chunk.chunk_id for chunk in result.found] == ["mysql-1"]
        assert result.missing == ["missing"]
        assert result.version_mismatch == ["redis-1"]
    finally:
        _drop_table(dsn, table_name)


class StableScoreEmbeddingModel:
    def encode(self, text: str, normalize_embeddings: bool = True):
        lowered = text.lower()
        if "unrelated" in lowered:
            return [0.0, 1.0, 0.0]
        return [1.0, 0.0, 0.0]


@pytest.mark.pgvector
def test_search_filters_low_scores_and_sorts_equal_scores_by_chunk_id():
    dsn = os.getenv("POSTGRES_DSN")
    if not dsn:
        pytest.skip("POSTGRES_DSN is not configured")

    table_name = f"knowledge_chunks_{uuid.uuid4().hex[:8]}"
    store = PgVectorKnowledgeStore(
        dsn=dsn,
        table_name=table_name,
        embedding_model_name="test",
        embedding_dimension=3,
        embedding_model=StableScoreEmbeddingModel(),
        minimum_score=0.5,
    )
    chunks = [
        make_chunk(
            "redis-b",
            title="Redis B",
            content="Cache consistency evidence.",
            source_type="theory",
            domain="redis",
            tags=["redis"],
        ),
        make_chunk(
            "redis-a",
            title="Redis A",
            content="Cache invalidation evidence.",
            source_type="theory",
            domain="redis",
            tags=["redis"],
        ),
        make_chunk(
            "redis-unrelated",
            title="Unrelated material",
            content="Unrelated evidence.",
            source_type="theory",
            domain="redis",
            tags=["redis"],
        ),
    ]

    try:
        store.upsert_chunks(chunks)
        results = store.search(
            "Redis cache consistency",
            job_tags=["redis"],
            source_types=["theory"],
            limit=5,
        )

        assert [chunk.chunk_id for chunk in results] == ["redis-a", "redis-b"]
        assert all(chunk.score >= 0.5 for chunk in results)
    finally:
        _drop_table(dsn, table_name)


def _drop_table(dsn: str, table_name: str) -> None:
    psycopg2, _ = PgVectorKnowledgeStore._import_psycopg2()
    with psycopg2.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(f'DROP TABLE IF EXISTS "{table_name}"')
