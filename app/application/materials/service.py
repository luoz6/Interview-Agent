from __future__ import annotations

from datetime import datetime, timezone

from app.domain.knowledge.user_document import (
    UserDocument,
    UserDocumentPublicStatus,
)
from app.domain.knowledge.source_scope import KnowledgeScopeUsage
from app.ports.user_documents import UserDocumentStorePort


class UserMaterialsError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class UserDocumentService:
    def __init__(self, *, store: UserDocumentStorePort, clock=None) -> None:
        self._store = store
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def list_documents(self, *, owner_principal_id: str) -> tuple[UserDocument, ...]:
        return self._store.list_documents(owner_principal_id=owner_principal_id)

    def get_document(
        self, *, owner_principal_id: str, document_id: str
    ) -> UserDocument:
        document = self._store.get_document(
            owner_principal_id=owner_principal_id,
            document_id=document_id,
        )
        if document is None:
            raise UserMaterialsError("document_not_found")
        return document

    def rename_document(
        self,
        *,
        owner_principal_id: str,
        document_id: str,
        display_title: str,
    ) -> UserDocument:
        return self.patch_document(
            owner_principal_id=owner_principal_id,
            document_id=document_id,
            display_title=display_title,
        )

    def set_enabled(
        self,
        *,
        owner_principal_id: str,
        document_id: str,
        enabled: bool,
    ) -> UserDocument:
        return self.patch_document(
            owner_principal_id=owner_principal_id,
            document_id=document_id,
            enabled=enabled,
        )

    def patch_document(
        self,
        *,
        owner_principal_id: str,
        document_id: str,
        display_title: str | None = None,
        enabled: bool | None = None,
        allowed_usages: tuple[KnowledgeScopeUsage, ...] | None = None,
    ) -> UserDocument:
        current = self.get_document(
            owner_principal_id=owner_principal_id,
            document_id=document_id,
        )
        updates: dict[str, object] = {}
        if display_title is not None:
            updates["display_title"] = display_title
        if allowed_usages is not None:
            updates["allowed_usages"] = allowed_usages
        if enabled is not None:
            if current.public_status not in {
                UserDocumentPublicStatus.READY,
                UserDocumentPublicStatus.DISABLED,
            }:
                raise UserMaterialsError("retry_not_allowed")
            updates.update(
                {
                    "enabled": enabled,
                    "public_status": (
                        UserDocumentPublicStatus.READY
                        if enabled
                        else UserDocumentPublicStatus.DISABLED
                    ),
                }
            )
        if not updates or all(
            getattr(current, field_name) == value
            for field_name, value in updates.items()
        ):
            return current
        updated = UserDocument.model_validate(
            {
                **current.model_dump(mode="python"),
                **updates,
                "updated_at": self._clock(),
            }
        )
        return self._save(owner_principal_id, updated)

    def _save(self, owner_principal_id: str, document: UserDocument) -> UserDocument:
        saved = self._store.save_document(
            owner_principal_id=owner_principal_id,
            document=document,
        )
        if saved is None:
            raise UserMaterialsError("document_not_found")
        return saved
