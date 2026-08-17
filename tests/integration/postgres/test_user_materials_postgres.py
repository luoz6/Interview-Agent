from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import pytest
from psycopg2 import sql

from app.adapters.pgvector.user_document_repository import (
    PgVectorUserDocumentChunkRepository,
)
from app.adapters.postgres.user_documents import PostgresUserDocumentStore
from app.adapters.postgres.user_materials_schema import (
    migrate_user_materials_schema,
    user_materials_relation_names,
    validate_user_materials_schema,
)
from app.application.materials.deletion_service import UserDocumentDeletionService
from app.application.materials.ingestion_service import UserDocumentIngestionService
from app.application.materials.service import UserDocumentService, UserMaterialsError
from app.domain.knowledge.user_document import UserDocumentPublicStatus
from app.services.embedding_providers import EmbeddingProviderError
from app.services.postgres_connections import DirectPsycopg2ConnectionProvider
from tests.vector_store_fixtures import FakeEmbeddingProvider


pytestmark = pytest.mark.pg_runtime

EMBEDDING_DIMENSION = 3
OWNER_A = "principal-a"
OWNER_B = "principal-b"
NOW = datetime(2026, 8, 17, tzinfo=timezone.utc)
LIFECYCLE_TEXT = (
    "# Redis cache aside\n\n"
    "PostgreSQL transactions preserve durable application state."
)
LIFECYCLE_CONTENT = LIFECYCLE_TEXT.encode()
FAILED_TEXT = "Redis cache aside"
FAILED_CONTENT = FAILED_TEXT.encode()


class FlakyEmbeddingProvider(FakeEmbeddingProvider):
    def __init__(self) -> None:
        self.document_calls = 0

    def embed_documents(self, texts):
        self.document_calls += 1
        if self.document_calls == 1:
            raise EmbeddingProviderError("synthetic_unavailable", retryable=True)
        return super().embed_documents(texts)


@dataclass(frozen=True)
class MaterialsPostgresHarness:
    provider: DirectPsycopg2ConnectionProvider
    table_prefix: str
    documents_table: str
    revisions_table: str
    chunks_table: str
    store: PostgresUserDocumentStore
    chunks: PgVectorUserDocumentChunkRepository


def _runtime_adapters(provider, table_prefix):
    return (
        PostgresUserDocumentStore(
            connection_provider=provider,
            table_prefix=table_prefix,
            schema_mode="validate",
        ),
        PgVectorUserDocumentChunkRepository(
            connection_provider=provider,
            table_prefix=table_prefix,
            embedding_dimension=EMBEDDING_DIMENSION,
            schema_mode="validate",
        ),
    )


@pytest.fixture
def materials_postgres_harness(
    postgres_dsn,
    runtime_table_prefix,
) -> MaterialsPostgresHarness:
    provider = DirectPsycopg2ConnectionProvider(
        postgres_dsn,
        connect_kwargs={"connect_timeout": 3},
    )
    migrate_user_materials_schema(
        provider,
        table_prefix=runtime_table_prefix,
        embedding_dimension=EMBEDDING_DIMENSION,
    )
    validate_user_materials_schema(
        provider,
        table_prefix=runtime_table_prefix,
    )
    store, chunks = _runtime_adapters(provider, runtime_table_prefix)
    documents_table, revisions_table, chunks_table = user_materials_relation_names(
        runtime_table_prefix
    )
    return MaterialsPostgresHarness(
        provider=provider,
        table_prefix=runtime_table_prefix,
        documents_table=documents_table,
        revisions_table=revisions_table,
        chunks_table=chunks_table,
        store=store,
        chunks=chunks,
    )


def _ingestion(harness, embedder):
    assert embedder.dimension == EMBEDDING_DIMENSION
    return UserDocumentIngestionService(
        store=harness.store,
        chunks=harness.chunks,
        embedder=embedder,
        clock=lambda: NOW,
    )


def _fetchall(provider, statement, parameters=()):
    with provider.connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(statement, parameters)
            return cursor.fetchall()


def _business_row_counts(harness) -> tuple[int, int, int]:
    statement = sql.SQL(
        """
        SELECT
            (SELECT COUNT(*) FROM {documents}),
            (SELECT COUNT(*) FROM {revisions}),
            (SELECT COUNT(*) FROM {chunks})
        """
    ).format(
        documents=sql.Identifier(harness.documents_table),
        revisions=sql.Identifier(harness.revisions_table),
        chunks=sql.Identifier(harness.chunks_table),
    )
    row = _fetchall(harness.provider, statement)[0]
    return tuple(int(value) for value in row)


def _embedding_storage_type(harness) -> str:
    rows = _fetchall(
        harness.provider,
        """
        SELECT format_type(attribute.atttypid, attribute.atttypmod)
        FROM pg_catalog.pg_attribute AS attribute
        WHERE attribute.attrelid = %s::regclass
          AND attribute.attname = 'embedding'
          AND attribute.attnum > 0
          AND NOT attribute.attisdropped
        """,
        (harness.chunks_table,),
    )
    assert len(rows) == 1
    return str(rows[0][0])


def _material_rows(harness):
    documents = _fetchall(
        harness.provider,
        sql.SQL(
            """
            SELECT owner_principal_id, document_id::text, public_status,
                   enabled, allowed_usages, active_revision_id::text,
                   safe_error_code
            FROM {documents}
            ORDER BY owner_principal_id, document_id
            """
        ).format(documents=sql.Identifier(harness.documents_table)),
    )
    revisions = _fetchall(
        harness.provider,
        sql.SQL(
            """
            SELECT owner_principal_id, document_id::text,
                   document_revision_id::text, revision
            FROM {revisions}
            ORDER BY owner_principal_id, document_id, revision
            """
        ).format(revisions=sql.Identifier(harness.revisions_table)),
    )
    chunks = _fetchall(
        harness.provider,
        sql.SQL(
            """
            SELECT owner_principal_id, document_id::text,
                   document_revision_id::text, position,
                   vector_dims(embedding), lexical_document::text
            FROM {chunks}
            ORDER BY owner_principal_id, document_id, position
            """
        ).format(chunks=sql.Identifier(harness.chunks_table)),
    )
    return documents, revisions, chunks


def _assert_business_relations_empty(harness) -> None:
    # Successful SELECTs prove the three relations still exist before the
    # shared owned-scope teardown removes them and verifies its cleanup receipt.
    assert _business_row_counts(harness) == (0, 0, 0)


def test_operator_migration_is_idempotent_and_runtime_is_validate_only(
    materials_postgres_harness,
):
    harness = materials_postgres_harness

    migrate_user_materials_schema(
        harness.provider,
        table_prefix=harness.table_prefix,
        embedding_dimension=EMBEDDING_DIMENSION,
    )
    validate_user_materials_schema(
        harness.provider,
        table_prefix=harness.table_prefix,
    )
    restarted_store, restarted_chunks = _runtime_adapters(
        harness.provider,
        harness.table_prefix,
    )

    assert harness.store.schema_mode == restarted_store.schema_mode == "validate"
    assert harness.chunks.schema_mode == restarted_chunks.schema_mode == "validate"
    assert restarted_chunks.embedding_dimension == EMBEDDING_DIMENSION
    assert _embedding_storage_type(harness) == "vector(3)"
    _assert_business_relations_empty(harness)


def test_user_material_lifecycle_is_owner_scoped_and_fully_deleted(
    materials_postgres_harness,
):
    harness = materials_postgres_harness
    embedder = FakeEmbeddingProvider()
    ingestion = _ingestion(harness, embedder)
    document = ingestion.ingest(
        owner_principal_id=OWNER_A,
        original_filename="notes/redis-postgres.md",
        media_type="text/markdown",
        content=LIFECYCLE_CONTENT,
        display_title="Redis and PostgreSQL Notes",
    )
    revision_id = document.active_revision_id
    assert document.public_status is UserDocumentPublicStatus.READY
    assert document.enabled is True
    assert revision_id is not None

    documents = UserDocumentService(store=harness.store, clock=lambda: NOW)
    assert documents.list_documents(owner_principal_id=OWNER_A) == (document,)
    assert documents.list_documents(owner_principal_id=OWNER_B) == ()
    with pytest.raises(UserMaterialsError) as missing:
        documents.get_document(
            owner_principal_id=OWNER_B,
            document_id=document.document_id,
        )
    assert missing.value.code == "document_not_found"
    assert harness.store.get_revision(
        owner_principal_id=OWNER_B,
        document_revision_id=revision_id,
    ) is None
    assert harness.chunks.list_revision_chunks(
        owner_principal_id=OWNER_B,
        document_revision_id=revision_id,
    ) == ()
    assert harness.chunks.search_lexical(
        owner_principal_id=OWNER_B,
        allowed_document_revision_ids=(revision_id,),
        query_text="Redis",
        limit=5,
    ) == ()
    assert harness.chunks.search_semantic(
        owner_principal_id=OWNER_B,
        allowed_document_revision_ids=(revision_id,),
        query_embedding=tuple(embedder.embed_query("Redis cache")),
        limit=5,
    ) == ()

    restarted_store, restarted_chunks = _runtime_adapters(
        harness.provider,
        harness.table_prefix,
    )
    restarted_documents = UserDocumentService(
        store=restarted_store,
        clock=lambda: NOW,
    )
    restarted_deletion = UserDocumentDeletionService(
        store=restarted_store,
        chunks=restarted_chunks,
        clock=lambda: NOW,
    )
    assert restarted_store.get_document(
        owner_principal_id=OWNER_A,
        document_id=document.document_id,
    ) == document
    revisions = restarted_store.list_revisions(
        owner_principal_id=OWNER_A,
        document_id=document.document_id,
    )
    stored_chunks = restarted_chunks.list_revision_chunks(
        owner_principal_id=OWNER_A,
        document_revision_id=revision_id,
    )
    assert len(revisions) == 1
    assert revisions[0].document_revision_id == revision_id
    assert restarted_store.get_revision_content(
        owner_principal_id=OWNER_A,
        document_revision_id=revision_id,
    ) == (LIFECYCLE_CONTENT, LIFECYCLE_TEXT)
    assert [chunk.position for chunk in stored_chunks] == [1, 2]

    patched = restarted_documents.patch_document(
        owner_principal_id=OWNER_A,
        document_id=document.document_id,
        display_title="Redis / PostgreSQL Interview Notes",
        allowed_usages=("feedback", "question"),
    )
    disabled = restarted_documents.set_enabled(
        owner_principal_id=OWNER_A,
        document_id=document.document_id,
        enabled=False,
    )
    enabled = restarted_documents.set_enabled(
        owner_principal_id=OWNER_A,
        document_id=document.document_id,
        enabled=True,
    )
    assert patched.allowed_usages == ("question", "feedback")
    assert disabled.public_status is UserDocumentPublicStatus.DISABLED
    assert disabled.enabled is False
    assert enabled.public_status is UserDocumentPublicStatus.READY
    assert enabled.enabled is True

    lexical_hits = restarted_chunks.search_lexical(
        owner_principal_id=OWNER_A,
        allowed_document_revision_ids=(revision_id,),
        query_text="transactions",
        limit=5,
    )
    semantic_hits = restarted_chunks.search_semantic(
        owner_principal_id=OWNER_A,
        allowed_document_revision_ids=(revision_id,),
        query_embedding=tuple(embedder.embed_query("Redis cache")),
        limit=5,
    )
    assert lexical_hits
    assert any("transactions" in hit.content for hit in lexical_hits)
    assert semantic_hits
    assert all(hit.owner_principal_id == OWNER_A for hit in semantic_hits)
    assert all(hit.document_revision_id == revision_id for hit in semantic_hits)

    document_rows, revision_rows, chunk_rows = _material_rows(harness)
    assert len(document_rows) == 1
    document_row = document_rows[0]
    assert document_row[:4] == (OWNER_A, document.document_id, "ready", True)
    assert tuple(document_row[4]) == ("question", "feedback")
    assert document_row[5:] == (revision_id, None)
    assert revision_rows == [(OWNER_A, document.document_id, revision_id, 1)]
    assert [row[:4] for row in chunk_rows] == [
        (OWNER_A, document.document_id, revision_id, 1),
        (OWNER_A, document.document_id, revision_id, 2),
    ]
    assert all(row[4] == EMBEDDING_DIMENSION and row[5] for row in chunk_rows)

    with pytest.raises(UserMaterialsError) as missing:
        restarted_deletion.delete(
            owner_principal_id=OWNER_B,
            document_id=document.document_id,
        )
    assert missing.value.code == "document_not_found"
    assert _business_row_counts(harness) == (1, 1, 2)

    deleted = restarted_deletion.delete(
        owner_principal_id=OWNER_A,
        document_id=document.document_id,
    )
    assert (
        deleted.deleted_revisions,
        deleted.deleted_payloads,
        deleted.deleted_chunks,
    ) == (1, 1, 2)
    assert restarted_store.get_document(
        owner_principal_id=OWNER_A,
        document_id=document.document_id,
    ) is None
    assert restarted_chunks.search_lexical(
        owner_principal_id=OWNER_A,
        allowed_document_revision_ids=(revision_id,),
        query_text="transactions",
        limit=5,
    ) == ()
    assert restarted_chunks.search_semantic(
        owner_principal_id=OWNER_A,
        allowed_document_revision_ids=(revision_id,),
        query_embedding=tuple(embedder.embed_query("Redis cache")),
        limit=5,
    ) == ()
    _assert_business_relations_empty(harness)


def test_failed_embedding_retry_reuses_revision_and_ready_retry_is_idempotent(
    materials_postgres_harness,
):
    harness = materials_postgres_harness
    embedder = FlakyEmbeddingProvider()
    ingestion = _ingestion(harness, embedder)

    failed = ingestion.ingest(
        owner_principal_id=OWNER_A,
        original_filename="redis.txt",
        media_type="text/plain",
        content=FAILED_CONTENT,
    )
    revision = harness.store.get_latest_revision(
        owner_principal_id=OWNER_A,
        document_id=failed.document_id,
    )
    assert revision is not None
    assert embedder.document_calls == 1
    assert failed.public_status is UserDocumentPublicStatus.FAILED
    assert failed.safe_error_code == "embedding_unavailable"
    assert failed.active_revision_id is None
    assert _business_row_counts(harness) == (1, 1, 0)
    document_rows, revision_rows, chunk_rows = _material_rows(harness)
    assert len(document_rows) == 1
    failed_row = document_rows[0]
    assert (
        failed_row[0],
        failed_row[1],
        failed_row[2],
        failed_row[5],
        failed_row[6],
    ) == (
        OWNER_A,
        failed.document_id,
        "failed",
        None,
        "embedding_unavailable",
    )
    assert revision_rows == [
        (OWNER_A, failed.document_id, revision.document_revision_id, 1)
    ]
    assert chunk_rows == []
    restarted_store, restarted_chunks = _runtime_adapters(
        harness.provider,
        harness.table_prefix,
    )
    assert restarted_store.get_revision_content(
        owner_principal_id=OWNER_A,
        document_revision_id=revision.document_revision_id,
    ) == (FAILED_CONTENT, FAILED_TEXT)
    assert restarted_chunks.list_revision_chunks(
        owner_principal_id=OWNER_A,
        document_revision_id=revision.document_revision_id,
    ) == ()
    with pytest.raises(UserMaterialsError) as missing:
        ingestion.retry(
            owner_principal_id=OWNER_B,
            document_id=failed.document_id,
        )
    assert missing.value.code == "document_not_found"

    ready = ingestion.retry(
        owner_principal_id=OWNER_A,
        document_id=failed.document_id,
    )
    replayed = ingestion.retry(
        owner_principal_id=OWNER_A,
        document_id=failed.document_id,
    )
    revisions = harness.store.list_revisions(
        owner_principal_id=OWNER_A,
        document_id=failed.document_id,
    )
    chunks = harness.chunks.list_revision_chunks(
        owner_principal_id=OWNER_A,
        document_revision_id=revision.document_revision_id,
    )
    assert ready.public_status is UserDocumentPublicStatus.READY
    assert ready.active_revision_id == revision.document_revision_id
    assert replayed == ready
    assert embedder.document_calls == 2
    assert len(revisions) == len(chunks) == 1
    assert revisions[0].document_revision_id == revision.document_revision_id
    assert _business_row_counts(harness) == (1, 1, 1)
    _, revision_rows, chunk_rows = _material_rows(harness)
    assert revision_rows == [
        (OWNER_A, failed.document_id, revision.document_revision_id, 1)
    ]
    assert [row[:4] for row in chunk_rows] == [
        (OWNER_A, failed.document_id, revision.document_revision_id, 1)
    ]
    assert chunk_rows[0][4] == EMBEDDING_DIMENSION
    assert chunk_rows[0][5]

    deletion = UserDocumentDeletionService(
        store=harness.store,
        chunks=harness.chunks,
        clock=lambda: NOW,
    )
    deleted = deletion.delete(
        owner_principal_id=OWNER_A,
        document_id=failed.document_id,
    )
    assert (
        deleted.deleted_revisions,
        deleted.deleted_payloads,
        deleted.deleted_chunks,
    ) == (1, 1, 1)
    assert harness.store.get_revision(
        owner_principal_id=OWNER_A,
        document_revision_id=revision.document_revision_id,
    ) is None
    assert harness.store.get_revision_content(
        owner_principal_id=OWNER_A,
        document_revision_id=revision.document_revision_id,
    ) is None
    assert harness.chunks.list_revision_chunks(
        owner_principal_id=OWNER_A,
        document_revision_id=revision.document_revision_id,
    ) == ()
    assert harness.chunks.search_lexical(
        owner_principal_id=OWNER_A,
        allowed_document_revision_ids=(revision.document_revision_id,),
        query_text="Redis",
        limit=5,
    ) == ()
    assert harness.chunks.search_semantic(
        owner_principal_id=OWNER_A,
        allowed_document_revision_ids=(revision.document_revision_id,),
        query_embedding=tuple(embedder.embed_query("Redis cache")),
        limit=5,
    ) == ()
    _assert_business_relations_empty(harness)
