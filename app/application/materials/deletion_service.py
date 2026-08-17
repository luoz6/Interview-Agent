from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from app.application.materials.service import UserMaterialsError
from app.domain.knowledge.user_document import UserDocumentPublicStatus
from app.ports.user_documents import (
    UserDocumentChunkRepositoryPort,
    UserDocumentStorePort,
)


@dataclass(frozen=True)
class UserDocumentDeletionResult:
    document_id: str
    deleted_revisions: int
    deleted_payloads: int
    deleted_chunks: int


class UserDocumentDeletionService:
    def __init__(
        self,
        *,
        store: UserDocumentStorePort,
        chunks: UserDocumentChunkRepositoryPort,
        clock=None,
    ) -> None:
        self._store = store
        self._chunks = chunks
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def delete(
        self, *, owner_principal_id: str, document_id: str
    ) -> UserDocumentDeletionResult:
        current = self._store.get_document(
            owner_principal_id=owner_principal_id,
            document_id=document_id,
        )
        if current is None:
            raise UserMaterialsError("document_not_found")
        deleting = current.model_copy(
            update={
                "public_status": UserDocumentPublicStatus.DELETING,
                "enabled": False,
                "safe_error_code": None,
                "updated_at": self._clock(),
            }
        )
        if self._store.save_document(
            owner_principal_id=owner_principal_id,
            document=deleting,
        ) is None:
            raise UserMaterialsError("document_not_found")
        deleted_chunks = self._chunks.delete_by_document(
            owner_principal_id=owner_principal_id,
            document_id=document_id,
        )
        deleted = self._store.delete_document(
            owner_principal_id=owner_principal_id,
            document_id=document_id,
        )
        if deleted is None:
            raise UserMaterialsError("processing_failed")
        revisions, payloads = deleted
        return UserDocumentDeletionResult(
            document_id=document_id,
            deleted_revisions=revisions,
            deleted_payloads=payloads,
            deleted_chunks=deleted_chunks,
        )
