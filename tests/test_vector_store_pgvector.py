import hashlib
import json
import os
import uuid

import pytest

from app.services.vector_store import PgVectorKnowledgeStore
from tests.test_vector_store import FakeEmbeddingProvider


def require_dsn() -> str:
    dsn = os.getenv("POSTGRES_DSN")
    if not dsn:
        pytest.skip("POSTGRES_DSN is not configured")
    return dsn


def make_store(dsn: str, base: str | None = None) -> PgVectorKnowledgeStore:
    return PgVectorKnowledgeStore(
        dsn=dsn,
        table_name=base or f"knowledge_{uuid.uuid4().hex[:10]}",
        embedding_provider=FakeEmbeddingProvider(),
    )


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
