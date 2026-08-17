from datetime import datetime, timezone
from inspect import signature
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.adapters.memory.user_documents import (
    InMemoryUserDocumentChunkRepository,
    InMemoryUserDocumentStore,
)
from app.domain.knowledge.user_document import (
    UserDocument,
    UserDocumentChunk,
    UserDocumentInternalStage,
    UserDocumentPublicStatus,
    UserDocumentRevision,
)
from app.ports.user_documents import (
    UserDocumentChunkRepositoryPort,
    UserDocumentStorePort,
)


NOW = datetime(2026, 8, 15, tzinfo=timezone.utc)
OWNER_A = "principal-a"
OWNER_B = "principal-b"


def _document() -> UserDocument:
    return UserDocument(
        document_id=str(uuid4()),
        owner_principal_id=OWNER_A,
        display_title="Redis 笔记",
        original_filename="redis.md",
        media_type="text/markdown",
        size_bytes=18,
        public_status=UserDocumentPublicStatus.PROCESSING,
        internal_stage=UserDocumentInternalStage.EXTRACTION,
        created_at=NOW,
        updated_at=NOW,
    )


def _revision(document: UserDocument) -> UserDocumentRevision:
    revision_id = str(uuid4())
    return UserDocumentRevision(
        document_revision_id=revision_id,
        document_id=document.document_id,
        revision=1,
        original_file_sha256="a" * 64,
        content_sha256="b" * 64,
        extracted_text_ref=f"memory:user-material:{revision_id}",
        parser_version="utf8-text-v1",
        chunker_version="paragraph-v1",
        embedding_identity="fake:fake-bge-m3:fake-v1:3",
        created_at=NOW,
    )


def _chunk(document: UserDocument, revision: UserDocumentRevision) -> UserDocumentChunk:
    return UserDocumentChunk(
        chunk_id=str(uuid4()),
        owner_principal_id=OWNER_A,
        document_id=document.document_id,
        document_revision_id=revision.document_revision_id,
        position=1,
        title=document.display_title,
        content="Redis 使用缓存旁路模式。",
        content_sha256="c" * 64,
        embedding=(0.1, 0.2, 0.3),
        embedding_identity=revision.embedding_identity,
        created_at=NOW,
    )


def test_in_memory_adapters_implement_exactly_two_runtime_ports():
    assert isinstance(InMemoryUserDocumentStore(), UserDocumentStorePort)
    assert isinstance(
        InMemoryUserDocumentChunkRepository(),
        UserDocumentChunkRepositoryPort,
    )


def test_every_public_port_operation_requires_owner_scope():
    for port in (UserDocumentStorePort, UserDocumentChunkRepositoryPort):
        operations = [
            value
            for name, value in vars(port).items()
            if callable(value) and not name.startswith("_")
        ]
        assert operations
        assert all(
            "owner_principal_id" in signature(operation).parameters
            for operation in operations
        )


def test_document_store_and_chunk_repository_isolate_synthetic_principals():
    store = InMemoryUserDocumentStore()
    repository = InMemoryUserDocumentChunkRepository()
    document = _document()
    revision = _revision(document)
    chunk = _chunk(document, revision)

    store.create_document(owner_principal_id=OWNER_A, document=document)
    store.create_revision(
        owner_principal_id=OWNER_A,
        revision=revision,
        original_content=b"Redis cache aside",
        extracted_text="Redis cache aside",
    )
    repository.replace_revision_chunks(
        owner_principal_id=OWNER_A,
        document_id=document.document_id,
        document_revision_id=revision.document_revision_id,
        chunks=(chunk,),
    )

    assert store.get_document(
        owner_principal_id=OWNER_B,
        document_id=document.document_id,
    ) is None
    assert store.get_revision(
        owner_principal_id=OWNER_B,
        document_revision_id=revision.document_revision_id,
    ) is None
    assert store.get_revision_content(
        owner_principal_id=OWNER_B,
        document_revision_id=revision.document_revision_id,
    ) is None
    assert store.list_documents(owner_principal_id=OWNER_B) == ()
    assert repository.list_revision_chunks(
        owner_principal_id=OWNER_B,
        document_revision_id=revision.document_revision_id,
    ) == ()
    assert repository.search_semantic(
        owner_principal_id=OWNER_B,
        allowed_document_revision_ids=(revision.document_revision_id,),
        query_embedding=(0.1, 0.2, 0.3),
        limit=5,
    ) == ()
    assert repository.search_lexical(
        owner_principal_id=OWNER_B,
        allowed_document_revision_ids=(revision.document_revision_id,),
        query_text="Redis",
        limit=5,
    ) == ()
    assert repository.delete_by_document(
        owner_principal_id=OWNER_B,
        document_id=document.document_id,
    ) == 0
    assert repository.list_revision_chunks(
        owner_principal_id=OWNER_A,
        document_revision_id=revision.document_revision_id,
    ) == (chunk,)


def test_store_revalidates_copied_models_before_accepting_state():
    store = InMemoryUserDocumentStore()
    document = _document()
    store.create_document(owner_principal_id=OWNER_A, document=document)
    forged = document.model_copy(
        update={
            "public_status": UserDocumentPublicStatus.READY,
            "internal_stage": None,
        }
    )

    with pytest.raises(ValidationError, match="active revision"):
        store.save_document(owner_principal_id=OWNER_A, document=forged)
