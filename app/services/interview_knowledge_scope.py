from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from app.domain.knowledge.source_scope import (
    InterviewKnowledgeScopeSnapshot,
    SelectedUserDocumentRevision,
)
from app.domain.knowledge.user_document import UserDocumentPublicStatus
from app.ports.user_documents import UserDocumentStorePort
from app.services.interview_plan_revision import (
    build_interview_knowledge_scope_snapshot,
)


class InterviewKnowledgeScopeError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class InterviewKnowledgeScopeResolver:
    """Resolve and revalidate one owner-scoped immutable interview scope."""

    def __init__(self, *, store: UserDocumentStorePort, clock=None) -> None:
        self._store = store
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def resolve(
        self,
        *,
        owner_principal_id: str,
        selected_document_ids: tuple[str, ...],
        include_system_knowledge: bool,
    ) -> InterviewKnowledgeScopeSnapshot:
        document_ids = tuple(
            sorted(_document_id(value) for value in selected_document_ids)
        )
        if len(document_ids) != len(set(document_ids)):
            raise InterviewKnowledgeScopeError(
                "knowledge_scope_duplicate_document"
            )

        selected = tuple(
            self._resolve_document(
                owner_principal_id=owner_principal_id,
                document_id=document_id,
            )
            for document_id in document_ids
        )
        return build_interview_knowledge_scope_snapshot(
            include_system_knowledge=include_system_knowledge,
            selected_documents=selected,
            created_at=self._clock(),
        )

    def validate_snapshot(
        self,
        *,
        owner_principal_id: str,
        snapshot: InterviewKnowledgeScopeSnapshot,
    ) -> InterviewKnowledgeScopeSnapshot:
        validated = InterviewKnowledgeScopeSnapshot.model_validate(
            snapshot.model_dump(mode="json")
        )
        for frozen in validated.selected_documents:
            current = self._resolve_document(
                owner_principal_id=owner_principal_id,
                document_id=frozen.document_id,
            )
            if current != frozen:
                raise InterviewKnowledgeScopeError(
                    "knowledge_scope_document_unavailable"
                )
        return validated

    def _resolve_document(
        self,
        *,
        owner_principal_id: str,
        document_id: str,
    ) -> SelectedUserDocumentRevision:
        document = self._store.get_document(
            owner_principal_id=owner_principal_id,
            document_id=document_id,
        )
        if document is None:
            raise InterviewKnowledgeScopeError(
                "knowledge_scope_document_unavailable"
            )
        if (
            document.public_status != UserDocumentPublicStatus.READY
            or not document.enabled
            or document.active_revision_id is None
        ):
            raise InterviewKnowledgeScopeError(
                "knowledge_scope_document_unavailable"
            )
        revision = self._store.get_revision(
            owner_principal_id=owner_principal_id,
            document_revision_id=document.active_revision_id,
        )
        if revision is None or revision.document_id != document.document_id:
            raise InterviewKnowledgeScopeError(
                "knowledge_scope_document_unavailable"
            )
        return SelectedUserDocumentRevision(
            document_id=document.document_id,
            document_revision_id=revision.document_revision_id,
            content_sha256=revision.content_sha256,
            allowed_usages=document.allowed_usages,
        )


def _document_id(value: str) -> str:
    try:
        return str(UUID(str(value)))
    except (TypeError, ValueError, AttributeError) as exc:
        raise InterviewKnowledgeScopeError(
            "knowledge_scope_document_unavailable"
        ) from exc


__all__ = [
    "InterviewKnowledgeScopeError",
    "InterviewKnowledgeScopeResolver",
]
