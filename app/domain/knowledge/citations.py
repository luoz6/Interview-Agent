from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from app.domain.knowledge.evidence import EvidenceRef, SafeKnowledgeCitation
from app.domain.knowledge.source_scope import KnowledgeSourceScope
from app.domain.knowledge.user_document import UserDocument


def project_safe_knowledge_citations(
    *,
    source_scope: KnowledgeSourceScope,
    evidence_refs: Sequence[EvidenceRef],
    business_binding_evidence_ids: Iterable[str],
    final_evidence_ids: Iterable[str],
    consumed_evidence_ids: Iterable[str],
    documents_by_id: Mapping[str, UserDocument | None] | None = None,
) -> tuple[SafeKnowledgeCitation, ...]:
    """Project only Scope-valid evidence present in every consumption boundary."""

    validated_scope = KnowledgeSourceScope.model_validate(
        source_scope.model_dump(mode="json")
    )
    eligible_ids = (
        set(business_binding_evidence_ids)
        & set(final_evidence_ids)
        & set(consumed_evidence_ids)
    )
    documents = documents_by_id or {}
    citations: list[SafeKnowledgeCitation] = []
    seen_ids: set[str] = set()

    for raw_reference in evidence_refs:
        reference = EvidenceRef.model_validate(
            raw_reference.model_dump(mode="python")
        )
        if (
            reference.evidence_id not in eligible_ids
            or reference.evidence_id in seen_ids
        ):
            continue
        citation = _project_reference(
            validated_scope,
            reference,
            documents=documents,
        )
        if citation is None:
            continue
        seen_ids.add(reference.evidence_id)
        citations.append(citation)
    return tuple(citations)


def _project_reference(
    source_scope: KnowledgeSourceScope,
    reference: EvidenceRef,
    *,
    documents: Mapping[str, UserDocument | None],
) -> SafeKnowledgeCitation | None:
    if reference.source_type != "user_material":
        if not source_scope.include_system_knowledge:
            return None
        return SafeKnowledgeCitation(
            citation_id=_citation_id(source_scope, reference.evidence_id),
            source_scope="system_knowledge",
            display_title=_bounded_text(reference.title, limit=200),
            excerpt=_optional_bounded_text(reference.safe_excerpt, limit=500),
            usage=source_scope.usage,
            availability="available",
        )

    provenance = reference.provenance
    if provenance.get("knowledge_source") != "user_material":
        return None
    document_id = str(provenance.get("document_id") or "")
    revision_id = str(provenance.get("document_revision_id") or "")
    document_content_sha256 = str(
        provenance.get("document_content_sha256") or ""
    )
    frozen = source_scope.selected_document_by_revision_id.get(revision_id)
    if (
        frozen is None
        or frozen.document_id != document_id
        or frozen.content_sha256 != document_content_sha256
    ):
        return None

    document = documents.get(document_id)
    if (
        document is None
        or document.owner_principal_id != source_scope.owner_principal_id
        or document.document_id != document_id
    ):
        return _deleted_citation(source_scope, reference.evidence_id)

    if document.active_revision_id != revision_id:
        return SafeKnowledgeCitation(
            citation_id=_citation_id(source_scope, reference.evidence_id),
            source_scope="user_document",
            document_safe_ref=_document_safe_ref(document_id),
            display_title="资料暂不可用",
            excerpt=None,
            usage=source_scope.usage,
            availability="unavailable",
        )

    return SafeKnowledgeCitation(
        citation_id=_citation_id(source_scope, reference.evidence_id),
        source_scope="user_document",
        document_safe_ref=_document_safe_ref(document_id),
        display_title=_bounded_text(document.display_title, limit=200),
        excerpt=_optional_bounded_text(reference.safe_excerpt, limit=500),
        usage=source_scope.usage,
        availability="available",
    )


def _deleted_citation(
    source_scope: KnowledgeSourceScope,
    evidence_id: str,
) -> SafeKnowledgeCitation:
    return SafeKnowledgeCitation(
        citation_id=_citation_id(source_scope, evidence_id),
        source_scope="user_document",
        document_safe_ref=None,
        display_title="已删除资料",
        location_label=None,
        excerpt=None,
        usage=source_scope.usage,
        availability="deleted",
    )


def _citation_id(source_scope: KnowledgeSourceScope, evidence_id: str) -> str:
    opaque = uuid5(
        NAMESPACE_URL,
        ":".join(
            (
                "interview-agent-safe-citation-v1",
                source_scope.source_scope_sha256,
                source_scope.usage,
                evidence_id,
            )
        ),
    )
    return f"citation-{opaque.hex}"


def _document_safe_ref(document_id: str) -> str:
    opaque = uuid5(
        NAMESPACE_URL,
        f"interview-agent-material-safe-ref-v1:{document_id}",
    )
    return f"material-{opaque.hex}"


def _bounded_text(value: object, *, limit: int) -> str:
    normalized = " ".join(str(value or "").split())
    if not normalized:
        return "知识资料"
    return normalized[:limit].rstrip()


def _optional_bounded_text(value: object, *, limit: int) -> str | None:
    normalized = " ".join(str(value or "").split())
    if not normalized:
        return None
    return normalized[:limit].rstrip()


__all__ = ["project_safe_knowledge_citations"]
