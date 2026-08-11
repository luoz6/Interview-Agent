import hashlib
import json
import os
import uuid

import pytest

from app.services.knowledge_ingestion import PreparedKnowledgeChunk
from app.adapters.pgvector.repository import KnowledgeChunk, PgVectorKnowledgeStore
from tests.vector_store_fixtures import FakeEmbeddingProvider


def require_dsn() -> str:
    dsn = os.getenv("POSTGRES_DSN")
    if not dsn:
        pytest.skip("POSTGRES_DSN is not configured")
    return dsn


def make_store(
    dsn: str,
    base: str | None = None,
    provider=None,
) -> PgVectorKnowledgeStore:
    return PgVectorKnowledgeStore(
        dsn=dsn,
        table_name=base or f"knowledge_{uuid.uuid4().hex[:10]}",
        embedding_provider=provider or FakeEmbeddingProvider(),
    )


class CountingEmbeddingProvider(FakeEmbeddingProvider):
    def __init__(self):
        self.query_calls = 0

    def embed_query(self, text):
        self.query_calls += 1
        return super().embed_query(text)


def drop_store_tables(store: PgVectorKnowledgeStore) -> None:
    psycopg2, sql = store._import_psycopg2()
    with psycopg2.connect(store.dsn) as connection:
        with connection.cursor() as cursor:
            for table in (
                store.versions_table,
                store.releases_table,
                store.legacy_table,
            ):
                cursor.execute(
                    sql.SQL("DROP TABLE IF EXISTS {table} CASCADE").format(
                        table=sql.Identifier(table)
                    )
                )


def create_legacy_table(store: PgVectorKnowledgeStore) -> None:
    psycopg2, sql = store._import_psycopg2()
    with psycopg2.connect(store.dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                sql.SQL(
                    """
                    CREATE TABLE {table} (
                        chunk_id TEXT PRIMARY KEY,
                        title TEXT NOT NULL,
                        content TEXT NOT NULL,
                        source_type TEXT NOT NULL,
                        domain TEXT NOT NULL,
                        tags JSONB NOT NULL,
                        metadata JSONB NOT NULL,
                        embedding VECTOR(3) NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                ).format(table=sql.Identifier(store.legacy_table))
            )


def insert_legacy_row(
    store: PgVectorKnowledgeStore,
    *,
    chunk_id: str,
    content: str,
    content_sha256: str | None,
    vector: str,
) -> None:
    psycopg2, sql = store._import_psycopg2()
    metadata = {"source": "legacy-test"}
    if content_sha256 is not None:
        metadata["content_sha256"] = content_sha256
    with psycopg2.connect(store.dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                sql.SQL(
                    """
                    INSERT INTO {table} (
                        chunk_id, title, content, source_type, domain,
                        tags, metadata, embedding
                    )
                    VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::vector)
                    """
                ).format(table=sql.Identifier(store.legacy_table)),
                (
                    chunk_id,
                    f"Title {chunk_id}",
                    content,
                    "theory",
                    "redis",
                    json.dumps(["redis"]),
                    json.dumps(metadata),
                    vector,
                ),
            )


def make_prepared(chunk_id: str, content_hash: str, vector=None):
    return PreparedKnowledgeChunk(
        chunk=KnowledgeChunk(
            chunk_id=chunk_id,
            title=f"Title {chunk_id}",
            content=f"Content {chunk_id}",
            source_type="theory",
            domain="redis",
            tags=["redis"],
            metadata={"content_sha256": content_hash},
        ),
        content_sha256=content_hash,
        embedding=vector or [0.1, 0.2, 0.3],
    )


@pytest.mark.pgvector
def test_versioned_schema_has_one_active_index_restrict_fk_and_no_legacy_table():
    store = make_store(require_dsn())
    try:
        store.ensure_schema()
        psycopg2, _ = store._import_psycopg2()
        with psycopg2.connect(store.dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT to_regclass(%s), to_regclass(%s), to_regclass(%s)",
                    (
                        f"public.{store.versions_table}",
                        f"public.{store.releases_table}",
                        f"public.{store.legacy_table}",
                    ),
                )
                versions, releases, legacy = cursor.fetchone()
                cursor.execute(
                    """
                    SELECT COUNT(*)
                    FROM pg_indexes
                    WHERE schemaname = 'public'
                      AND tablename = %s
                      AND indexdef ILIKE '%%UNIQUE%%'
                      AND indexdef ILIKE '%%WHERE (status = ''active''::text)%%'
                    """,
                    (store.releases_table,),
                )
                active_indexes = int(cursor.fetchone()[0])
                cursor.execute(
                    """
                    SELECT confdeltype
                    FROM pg_constraint
                    WHERE conrelid = %s::regclass AND contype = 'f'
                    """,
                    (store.versions_table,),
                )
                delete_rule = cursor.fetchone()[0]

        assert versions == store.versions_table
        assert releases == store.releases_table
        assert legacy is None
        assert active_indexes == 1
        assert delete_rule == "r"
    finally:
        drop_store_tables(store)


@pytest.mark.pgvector
def test_empty_legacy_table_migrates_zero_rows_without_release():
    store = make_store(require_dsn())
    try:
        store.ensure_schema()
        create_legacy_table(store)

        assert store.migrate_legacy_rows() == 0

        psycopg2, sql = store._import_psycopg2()
        with psycopg2.connect(store.dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL("SELECT COUNT(*) FROM {table}").format(
                        table=sql.Identifier(store.releases_table)
                    )
                )
                assert cursor.fetchone()[0] == 0
    finally:
        drop_store_tables(store)


@pytest.mark.pgvector
def test_legacy_copy_is_truthful_retired_and_idempotent():
    store = make_store(require_dsn())
    first_hash = "a" * 64
    second_content = "Legacy content with normalized line endings.\r\n"
    second_hash = hashlib.sha256(second_content.strip().encode("utf-8")).hexdigest()
    try:
        store.ensure_schema()
        create_legacy_table(store)
        insert_legacy_row(
            store,
            chunk_id="legacy-a",
            content="First content",
            content_sha256=first_hash,
            vector="[0.1,0.2,0.3]",
        )
        insert_legacy_row(
            store,
            chunk_id="legacy-b",
            content=second_content,
            content_sha256=None,
            vector="[0.3,0.2,0.1]",
        )

        assert store.migrate_legacy_rows() == 2
        assert store.migrate_legacy_rows() == 2

        psycopg2, sql = store._import_psycopg2()
        with psycopg2.connect(store.dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        """
                        SELECT status, embedding_provider, embedding_model,
                               embedding_revision, embedding_dimension, chunk_count
                        FROM {table}
                        WHERE corpus_version = 'legacy-stage42-v1'
                        """
                    ).format(table=sql.Identifier(store.releases_table))
                )
                release = cursor.fetchone()
                cursor.execute(
                    sql.SQL(
                        """
                        SELECT chunk_id, content_sha256, embedding::text
                        FROM {table}
                        ORDER BY chunk_id
                        """
                    ).format(table=sql.Identifier(store.versions_table))
                )
                rows = cursor.fetchall()

        assert release == (
            "retired",
            "legacy-unknown",
            "legacy-unknown",
            "legacy-stage42-v1",
            3,
            2,
        )
        assert [(row[0], row[1]) for row in rows] == [
            ("legacy-a", first_hash),
            ("legacy-b", second_hash),
        ]
        assert [json.loads(row[2]) for row in rows] == [
            [0.1, 0.2, 0.3],
            [0.3, 0.2, 0.1],
        ]
    finally:
        drop_store_tables(store)


@pytest.mark.pgvector
def test_changed_legacy_content_under_same_release_is_rejected():
    store = make_store(require_dsn())
    try:
        store.ensure_schema()
        create_legacy_table(store)
        insert_legacy_row(
            store,
            chunk_id="legacy-a",
            content="First content",
            content_sha256="a" * 64,
            vector="[0.1,0.2,0.3]",
        )
        assert store.migrate_legacy_rows() == 1

        psycopg2, sql = store._import_psycopg2()
        with psycopg2.connect(store.dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL("UPDATE {table} SET content = %s WHERE chunk_id = %s").format(
                        table=sql.Identifier(store.legacy_table)
                    ),
                    ("Changed content", "legacy-a"),
                )

        with pytest.raises(ValueError, match="legacy corpus identity conflict"):
            store.migrate_legacy_rows()
    finally:
        drop_store_tables(store)


@pytest.mark.pgvector
def test_activation_is_idempotent_and_reuses_only_exact_identity_and_hash():
    store = make_store(require_dsn())
    chunks = [
        make_prepared("redis-a", "a" * 64, [0.1, 0.2, 0.3]),
        make_prepared("redis-b", "b" * 64, [0.3, 0.2, 0.1]),
    ]
    try:
        store.activate_corpus(
            corpus_version="stage44a-v1",
            manifest_sha256="1" * 64,
            provider=store.embedding_provider,
            chunks=chunks,
        )
        store.activate_corpus(
            corpus_version="stage44a-v1",
            manifest_sha256="1" * 64,
            provider=store.embedding_provider,
            chunks=chunks,
        )

        reusable = store.find_reusable_embeddings(
            [item.chunk for item in chunks],
            provider_name="fake",
            model_name="fake-bge-m3",
            model_revision="fake-v1",
            dimension=3,
        )
        changed = make_prepared("redis-a", "c" * 64).chunk
        changed_reusable = store.find_reusable_embeddings(
            [changed, chunks[1].chunk],
            provider_name="fake",
            model_name="fake-bge-m3",
            model_revision="fake-v1",
            dimension=3,
        )
        wrong_model = store.find_reusable_embeddings(
            [item.chunk for item in chunks],
            provider_name="fake",
            model_name="different-model",
            model_revision="fake-v1",
            dimension=3,
        )

        assert store.get_active_corpus_version() == "stage44a-v1"
        assert store.count_chunks() == 2
        assert reusable == {
            "redis-a": pytest.approx([0.1, 0.2, 0.3]),
            "redis-b": pytest.approx([0.3, 0.2, 0.1]),
        }
        assert set(changed_reusable) == {"redis-b"}
        assert wrong_model == {}
    finally:
        drop_store_tables(store)


@pytest.mark.pgvector
def test_activation_conflict_rolls_back_and_new_release_retires_old_without_deleting():
    store = make_store(require_dsn())
    v1_chunks = [make_prepared("redis-a", "a" * 64)]
    try:
        store.activate_corpus(
            corpus_version="stage44a-v1",
            manifest_sha256="1" * 64,
            provider=store.embedding_provider,
            chunks=v1_chunks,
        )

        with pytest.raises(ValueError, match="corpus version identity conflict"):
            store.activate_corpus(
                corpus_version="stage44a-v1",
                manifest_sha256="2" * 64,
                provider=store.embedding_provider,
                chunks=[make_prepared("redis-a", "changed")],
            )

        assert store.get_active_corpus_version() == "stage44a-v1"
        assert store.count_chunks() == 1

        store.activate_corpus(
            corpus_version="stage44a-v2",
            manifest_sha256="3" * 64,
            provider=store.embedding_provider,
            chunks=[make_prepared("redis-b", "b" * 64)],
        )

        psycopg2, sql = store._import_psycopg2()
        with psycopg2.connect(store.dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        """
                        SELECT corpus_version, status
                        FROM {releases}
                        ORDER BY corpus_version
                        """
                    ).format(releases=sql.Identifier(store.releases_table))
                )
                releases = cursor.fetchall()
                cursor.execute(
                    sql.SQL("SELECT COUNT(*) FROM {versions}").format(
                        versions=sql.Identifier(store.versions_table)
                    )
                )
                retained_rows = int(cursor.fetchone()[0])

        assert releases == [
            ("stage44a-v1", "retired"),
            ("stage44a-v2", "active"),
        ]
        assert retained_rows == 2
    finally:
        drop_store_tables(store)


@pytest.mark.pgvector
def test_search_reads_active_release_fetches_twelve_and_returns_five():
    provider = CountingEmbeddingProvider()
    store = make_store(require_dsn(), provider=provider)
    try:
        store.activate_corpus(
            corpus_version="stage44a-v1",
            manifest_sha256="1" * 64,
            provider=provider,
            chunks=[make_prepared("retired-only", "0" * 64)],
        )
        active_chunks = [
            make_prepared(
                f"redis-{index:02d}",
                f"{index:064x}",
                [0.1, 0.2, 0.3],
            )
            for index in range(13)
        ]
        store.activate_corpus(
            corpus_version="stage44a-v2",
            manifest_sha256="2" * 64,
            provider=provider,
            chunks=active_chunks,
        )

        results = store.search(
            "redis consistency",
            job_tags=["redis"],
            source_types=["theory"],
            limit=5,
        )

        assert [chunk.chunk_id for chunk in results] == [
            "redis-00",
            "redis-01",
            "redis-02",
            "redis-03",
            "redis-04",
        ]
        assert "retired-only" not in {chunk.chunk_id for chunk in results}
        assert provider.query_calls == 1
        assert store.last_search_trace["corpus_version"] == "stage44a-v2"
        assert store.last_search_trace["candidate_count"] == 12
    finally:
        drop_store_tables(store)


@pytest.mark.pgvector
def test_general_filter_fallback_does_not_earn_canonical_tag_boost():
    provider = CountingEmbeddingProvider()
    store = make_store(require_dsn(), provider=provider)
    prepared = make_prepared("fallback", "f" * 64, [0.1, 0.2, 0.3])
    prepared.chunk.tags[:] = ["general"]
    prepared.chunk.domain = "general"
    prepared.chunk.title = "Fallback material"
    try:
        store.activate_corpus(
            corpus_version="stage44a-v1",
            manifest_sha256="1" * 64,
            provider=provider,
            chunks=[prepared],
        )

        results = store.search(
            "unmatched",
            job_tags=["unknown"],
            limit=5,
        )

        query = [0.2, 0.3, 0.4]
        vector = [0.1, 0.2, 0.3]
        dense = sum(a * b for a, b in zip(query, vector)) / (
            sum(a * a for a in query) ** 0.5 * sum(b * b for b in vector) ** 0.5
        )
        assert len(results) == 1
        assert results[0].score == pytest.approx(dense)
    finally:
        drop_store_tables(store)


@pytest.mark.pgvector
def test_historical_lookup_uses_expected_hash_and_never_embeds():
    provider = CountingEmbeddingProvider()
    store = make_store(require_dsn(), provider=provider)
    old = make_prepared("redis-a", "a" * 64)
    new = make_prepared("redis-a", "b" * 64)
    new.chunk.content = "New active content"
    try:
        store.activate_corpus(
            corpus_version="stage44a-v1",
            manifest_sha256="1" * 64,
            provider=provider,
            chunks=[old],
        )
        store.activate_corpus(
            corpus_version="stage44a-v2",
            manifest_sha256="2" * 64,
            provider=provider,
            chunks=[new],
        )

        historical = store.get_by_ids(
            ["redis-a"],
            expected_hashes={"redis-a": "a" * 64},
        )
        active = store.get_by_ids(["redis-a"])
        mismatch = store.get_by_ids(
            ["redis-a"],
            expected_hashes={"redis-a": "c" * 64},
        )
        missing = store.get_by_ids(["absent"])

        assert historical.found[0].content == "Content redis-a"
        assert historical.missing == []
        assert historical.version_mismatch == []
        assert active.found[0].content == "New active content"
        assert mismatch.version_mismatch == ["redis-a"]
        assert mismatch.missing == []
        assert missing.missing == ["absent"]
        assert provider.query_calls == 0
    finally:
        drop_store_tables(store)
