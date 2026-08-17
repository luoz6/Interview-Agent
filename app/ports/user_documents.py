from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.domain.knowledge.user_document import (
    UserDocument,
    UserDocumentChunk,
    UserDocumentRevision,
)


@runtime_checkable
class UserDocumentStorePort(Protocol):
    def create_document(
        self, *, owner_principal_id: str, document: UserDocument
    ) -> UserDocument: ...

    def get_document(
        self, *, owner_principal_id: str, document_id: str
    ) -> UserDocument | None: ...

    def list_documents(
        self, *, owner_principal_id: str
    ) -> tuple[UserDocument, ...]: ...

    def save_document(
        self, *, owner_principal_id: str, document: UserDocument
    ) -> UserDocument | None: ...

    def create_revision(
        self,
        *,
        owner_principal_id: str,
        revision: UserDocumentRevision,
        original_content: bytes,
        extracted_text: str,
    ) -> UserDocumentRevision: ...

    def get_revision(
        self, *, owner_principal_id: str, document_revision_id: str
    ) -> UserDocumentRevision | None: ...

    def get_latest_revision(
        self, *, owner_principal_id: str, document_id: str
    ) -> UserDocumentRevision | None: ...

    def list_revisions(
        self, *, owner_principal_id: str, document_id: str
    ) -> tuple[UserDocumentRevision, ...]: ...

    def get_revision_content(
        self, *, owner_principal_id: str, document_revision_id: str
    ) -> tuple[bytes, str] | None: ...

    def delete_document(
        self, *, owner_principal_id: str, document_id: str
    ) -> tuple[int, int] | None: ...


@runtime_checkable
class UserDocumentChunkRepositoryPort(Protocol):
    def replace_revision_chunks(
        self,
        *,
        owner_principal_id: str,
        document_id: str,
        document_revision_id: str,
        chunks: tuple[UserDocumentChunk, ...],
    ) -> int: ...

    def list_revision_chunks(
        self, *, owner_principal_id: str, document_revision_id: str
    ) -> tuple[UserDocumentChunk, ...]: ...

    def search_semantic(
        self,
        *,
        owner_principal_id: str,
        allowed_document_revision_ids: tuple[str, ...],
        query_embedding: tuple[float, ...],
        limit: int,
    ) -> tuple[UserDocumentChunk, ...]: ...

    def search_lexical(
        self,
        *,
        owner_principal_id: str,
        allowed_document_revision_ids: tuple[str, ...],
        query_text: str,
        limit: int,
    ) -> tuple[UserDocumentChunk, ...]: ...

    def delete_by_revision(
        self, *, owner_principal_id: str, document_revision_id: str
    ) -> int: ...

    def delete_by_document(
        self, *, owner_principal_id: str, document_id: str
    ) -> int: ...
