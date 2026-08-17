from datetime import datetime, timezone

import pytest

from app.adapters.memory.user_documents import (
    InMemoryUserDocumentChunkRepository,
    InMemoryUserDocumentStore,
)
from app.application.materials.deletion_service import UserDocumentDeletionService
from app.application.materials.ingestion_service import UserDocumentIngestionService
from app.application.materials.service import UserDocumentService, UserMaterialsError
from app.domain.knowledge.user_document import (
    USER_DOCUMENT_MAX_BYTES,
    UserDocumentPublicStatus,
)
from app.services.embedding_providers import EmbeddingProviderError
from tests.vector_store_fixtures import FakeEmbeddingProvider


NOW = datetime(2026, 8, 15, tzinfo=timezone.utc)
OWNER_A = "principal-a"
OWNER_B = "principal-b"


class FlakyEmbeddingProvider(FakeEmbeddingProvider):
    def __init__(self) -> None:
        self.calls = 0

    def embed_documents(self, texts):
        self.calls += 1
        if self.calls == 1:
            raise EmbeddingProviderError("synthetic_unavailable", retryable=True)
        return super().embed_documents(texts)


def _services(embedder=None):
    store = InMemoryUserDocumentStore()
    chunks = InMemoryUserDocumentChunkRepository()
    clock = lambda: NOW
    ingestion = UserDocumentIngestionService(
        store=store,
        chunks=chunks,
        embedder=embedder or FakeEmbeddingProvider(),
        clock=clock,
    )
    documents = UserDocumentService(store=store, clock=clock)
    deletion = UserDocumentDeletionService(
        store=store,
        chunks=chunks,
        clock=clock,
    )
    return store, chunks, ingestion, documents, deletion


def test_markdown_ingest_reaches_ready_without_global_corpus_lifecycle():
    store, chunks, ingestion, _, _ = _services()

    document = ingestion.ingest(
        owner_principal_id=OWNER_A,
        original_filename="notes/redis.md",
        media_type="text/markdown",
        content="# Redis\r\n\r\n缓存旁路与一致性。".encode(),
    )

    assert document.public_status is UserDocumentPublicStatus.READY
    assert document.original_filename == "redis.md"
    assert document.active_revision_id is not None
    revisions = store.list_revisions(
        owner_principal_id=OWNER_A,
        document_id=document.document_id,
    )
    assert len(revisions) == 1
    assert revisions[0].document_revision_id == document.active_revision_id
    assert store.get_revision_content(
        owner_principal_id=OWNER_A,
        document_revision_id=document.active_revision_id,
    ) == (
        "# Redis\r\n\r\n缓存旁路与一致性。".encode(),
        "# Redis\n\n缓存旁路与一致性。",
    )
    stored_chunks = chunks.list_revision_chunks(
        owner_principal_id=OWNER_A,
        document_revision_id=document.active_revision_id,
    )
    assert [item.content for item in stored_chunks] == ["# Redis", "缓存旁路与一致性。"]
    assert all(item.embedding for item in stored_chunks)


@pytest.mark.parametrize(
    ("filename", "media_type", "content", "code"),
    (
        ("notes.pdf", "application/pdf", b"pdf", "unsupported_file_type"),
        ("notes.md", "text/plain", b"text", "unsupported_file_type"),
        ("notes.txt", "text/plain", b"\xff", "invalid_utf8"),
        ("notes.txt", "text/plain", b" \r\n ", "empty_document"),
        (
            "notes.txt",
            "text/plain",
            b"x" * (USER_DOCUMENT_MAX_BYTES + 1),
            "file_too_large",
        ),
    ),
    ids=(
        "unsupported-extension",
        "mime-mismatch",
        "invalid-utf8",
        "empty-document",
        "file-too-large",
    ),
)
def test_invalid_ingest_returns_stable_safe_code_without_creating_document(
    filename, media_type, content, code
):
    store, _, ingestion, _, _ = _services()

    with pytest.raises(UserMaterialsError) as exc_info:
        ingestion.ingest(
            owner_principal_id=OWNER_A,
            original_filename=filename,
            media_type=media_type,
            content=content,
        )

    assert exc_info.value.code == code
    assert store.list_documents(owner_principal_id=OWNER_A) == ()


def test_failed_embedding_retry_is_idempotent_and_publishes_one_active_revision():
    provider = FlakyEmbeddingProvider()
    store, chunks, ingestion, _, _ = _services(provider)
    failed = ingestion.ingest(
        owner_principal_id=OWNER_A,
        original_filename="redis.txt",
        media_type="text/plain",
        content=b"Redis cache aside",
    )

    assert failed.public_status is UserDocumentPublicStatus.FAILED
    assert failed.safe_error_code == "embedding_unavailable"
    assert failed.active_revision_id is None
    assert len(
        store.list_revisions(
            owner_principal_id=OWNER_A,
            document_id=failed.document_id,
        )
    ) == 1

    ready = ingestion.retry(
        owner_principal_id=OWNER_A,
        document_id=failed.document_id,
    )
    replay = ingestion.retry(
        owner_principal_id=OWNER_A,
        document_id=failed.document_id,
    )

    assert ready.public_status is UserDocumentPublicStatus.READY
    assert replay == ready
    assert provider.calls == 2
    assert len(
        store.list_revisions(
            owner_principal_id=OWNER_A,
            document_id=failed.document_id,
        )
    ) == 1
    assert len(
        chunks.list_revision_chunks(
            owner_principal_id=OWNER_A,
            document_revision_id=ready.active_revision_id,
        )
    ) == 1


def test_document_operations_are_owner_scoped_and_enablement_is_reversible():
    _, _, ingestion, documents, _ = _services()
    document = ingestion.ingest(
        owner_principal_id=OWNER_A,
        original_filename="redis.txt",
        media_type="text/plain",
        content=b"Redis cache aside",
    )

    with pytest.raises(UserMaterialsError) as exc_info:
        documents.get_document(
            owner_principal_id=OWNER_B,
            document_id=document.document_id,
        )
    assert exc_info.value.code == "document_not_found"

    renamed = documents.rename_document(
        owner_principal_id=OWNER_A,
        document_id=document.document_id,
        display_title="Redis 面试笔记",
    )
    disabled = documents.set_enabled(
        owner_principal_id=OWNER_A,
        document_id=document.document_id,
        enabled=False,
    )
    enabled = documents.set_enabled(
        owner_principal_id=OWNER_A,
        document_id=document.document_id,
        enabled=True,
    )

    assert renamed.display_title == "Redis 面试笔记"
    assert disabled.public_status is UserDocumentPublicStatus.DISABLED
    assert disabled.enabled is False
    assert enabled.public_status is UserDocumentPublicStatus.READY
    assert enabled.enabled is True


def test_deletion_clears_source_text_chunks_embeddings_and_future_hits():
    store, chunks, ingestion, _, deletion = _services()
    document = ingestion.ingest(
        owner_principal_id=OWNER_A,
        original_filename="redis.txt",
        media_type="text/plain",
        content=b"Redis cache aside",
    )
    revision_id = document.active_revision_id
    assert chunks.search_lexical(
        owner_principal_id=OWNER_A,
        allowed_document_revision_ids=(revision_id,),
        query_text="Redis",
        limit=5,
    )
    assert chunks.search_semantic(
        owner_principal_id=OWNER_A,
        allowed_document_revision_ids=(revision_id,),
        query_embedding=(0.1, 0.2, 0.3),
        limit=5,
    )

    result = deletion.delete(
        owner_principal_id=OWNER_A,
        document_id=document.document_id,
    )

    assert result.deleted_revisions == 1
    assert result.deleted_payloads == 1
    assert result.deleted_chunks == 1
    assert store.get_document(
        owner_principal_id=OWNER_A,
        document_id=document.document_id,
    ) is None
    assert store.get_revision(
        owner_principal_id=OWNER_A,
        document_revision_id=revision_id,
    ) is None
    assert store.get_revision_content(
        owner_principal_id=OWNER_A,
        document_revision_id=revision_id,
    ) is None
    assert chunks.list_revision_chunks(
        owner_principal_id=OWNER_A,
        document_revision_id=revision_id,
    ) == ()
    assert chunks.search_lexical(
        owner_principal_id=OWNER_A,
        allowed_document_revision_ids=(revision_id,),
        query_text="Redis",
        limit=5,
    ) == ()
    assert chunks.search_semantic(
        owner_principal_id=OWNER_A,
        allowed_document_revision_ids=(revision_id,),
        query_embedding=(0.1, 0.2, 0.3),
        limit=5,
    ) == ()


def test_cross_owner_delete_is_non_enumerable_and_preserves_owner_data():
    store, _, ingestion, _, deletion = _services()
    document = ingestion.ingest(
        owner_principal_id=OWNER_A,
        original_filename="redis.txt",
        media_type="text/plain",
        content=b"Redis cache aside",
    )

    with pytest.raises(UserMaterialsError) as exc_info:
        deletion.delete(
            owner_principal_id=OWNER_B,
            document_id=document.document_id,
        )

    assert exc_info.value.code == "document_not_found"
    assert store.get_document(
        owner_principal_id=OWNER_A,
        document_id=document.document_id,
    ) == document
