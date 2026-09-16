from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from app.domain.interview.session_plan_binding import session_plan_binding_from_state
from app.domain.knowledge.citations import project_safe_knowledge_citations
from app.domain.knowledge.evidence import SafeKnowledgeCitation
from app.domain.knowledge.source_scope import (
    InterviewKnowledgeScopeSnapshot,
    SelectedUserDocumentRevision,
    build_knowledge_source_scope,
)
from app.domain.knowledge.user_document import UserDocumentPublicStatus
from app.domain.report.models import InterviewReport


def sanitize_report_knowledge_citations_for_read(
    report_or_payload,
    *,
    session_state: Mapping[str, Any],
    user_document_store,
):
    """Return a read-only report projection with stale user citations redacted.

    Persisted reports and immutable artifacts remain unchanged. Every user-document
    citation is revalidated against the request session's frozen Plan binding and
    an Owner-bound current document/revision lookup. Any missing or unverifiable
    fact collapses to the same content-free deleted projection.
    """

    is_report_model = isinstance(report_or_payload, InterviewReport)
    payload = (
        report_or_payload.model_dump(mode="python")
        if is_report_model
        else deepcopy(dict(report_or_payload))
    )
    feedbacks = payload.get("feedbacks")
    if not isinstance(feedbacks, list):
        return (
            InterviewReport.model_validate(payload)
            if is_report_model
            else payload
        )

    has_user_citations = any(
        isinstance(raw_feedback, Mapping)
        and isinstance(raw_feedback.get("knowledge_citations"), list)
        and any(
            (
                raw_citation.source_scope == "user_document"
                if isinstance(raw_citation, SafeKnowledgeCitation)
                else isinstance(raw_citation, Mapping)
                and raw_citation.get("source_scope") == "user_document"
            )
            for raw_citation in raw_feedback["knowledge_citations"]
        )
        for raw_feedback in feedbacks
    )
    verification = (
        _read_time_user_citation_verification(
            session_state,
            user_document_store=user_document_store,
        )
        if has_user_citations
        else {}
    )
    sanitized_feedbacks: list[Any] = []
    for raw_feedback in feedbacks:
        if not isinstance(raw_feedback, Mapping):
            sanitized_feedbacks.append(deepcopy(raw_feedback))
            continue
        feedback = deepcopy(dict(raw_feedback))
        raw_citations = feedback.get("knowledge_citations")
        if not isinstance(raw_citations, list):
            sanitized_feedbacks.append(feedback)
            continue
        citations: list[dict[str, Any]] = []
        for raw_citation in raw_citations:
            try:
                citation = SafeKnowledgeCitation.model_validate(raw_citation)
            except (TypeError, ValueError):
                # Invalid legacy payloads are omitted instead of reflecting an
                # unvalidated object through a public report response.
                continue
            if citation.source_scope == "user_document":
                citation = _sanitize_user_citation_for_read(
                    citation,
                    verification=verification,
                )
            citations.append(citation.model_dump(mode="json"))
        feedback["knowledge_citations"] = citations
        sanitized_feedbacks.append(feedback)
    payload["feedbacks"] = sanitized_feedbacks
    return InterviewReport.model_validate(payload) if is_report_model else payload


def _read_time_user_citation_verification(
    session_state: Mapping[str, Any],
    *,
    user_document_store,
) -> dict[str, bool]:
    try:
        binding = session_plan_binding_from_state(dict(session_state))
        if (
            binding.plan_origin != "plan_revision"
            or binding.owner_principal_id is None
        ):
            return {}
        snapshot = InterviewKnowledgeScopeSnapshot.model_validate(
            binding.plan_snapshot["knowledge_scope"]
        )
        source_scope = build_knowledge_source_scope(
            snapshot,
            owner_principal_id=binding.owner_principal_id,
            usage="feedback",
        )
    except Exception:
        return {}

    return {
        _document_safe_ref(selected.document_id): _selected_document_is_accessible(
            selected,
            owner_principal_id=binding.owner_principal_id,
            user_document_store=user_document_store,
        )
        for selected in source_scope.selected_documents
    }


def _selected_document_is_accessible(
    selected: SelectedUserDocumentRevision,
    *,
    owner_principal_id: str,
    user_document_store,
) -> bool:
    if user_document_store is None:
        return False
    try:
        document = user_document_store.get_document(
            owner_principal_id=owner_principal_id,
            document_id=selected.document_id,
        )
        revision = user_document_store.get_revision(
            owner_principal_id=owner_principal_id,
            document_revision_id=selected.document_revision_id,
        )
    except Exception:
        return False
    try:
        return bool(
            document is not None
            and revision is not None
            and document.owner_principal_id == owner_principal_id
            and document.document_id == selected.document_id
            and document.public_status == UserDocumentPublicStatus.READY
            and document.enabled
            and "feedback" in document.allowed_usages
            and document.active_revision_id == selected.document_revision_id
            and revision.document_revision_id == selected.document_revision_id
            and revision.document_id == selected.document_id
            and revision.content_sha256 == selected.content_sha256
        )
    except Exception:
        return False


def _sanitize_user_citation_for_read(
    citation: SafeKnowledgeCitation,
    *,
    verification: Mapping[str, bool],
) -> SafeKnowledgeCitation:
    if citation.availability == "deleted":
        return citation
    safe_ref = citation.document_safe_ref
    if (
        citation.usage != "feedback"
        or safe_ref is None
        or verification.get(safe_ref) is not True
    ):
        return _deleted_read_citation(citation)
    return citation


def _deleted_read_citation(
    citation: SafeKnowledgeCitation,
) -> SafeKnowledgeCitation:
    return SafeKnowledgeCitation(
        citation_id=citation.citation_id,
        source_scope="user_document",
        document_safe_ref=None,
        display_title="已删除资料",
        location_label=None,
        excerpt=None,
        usage=citation.usage,
        availability="deleted",
    )


def _document_safe_ref(document_id: str) -> str:
    opaque = uuid5(
        NAMESPACE_URL,
        f"interview-agent-material-safe-ref-v1:{document_id}",
    )
    return f"material-{opaque.hex}"


__all__ = [
    "project_safe_knowledge_citations",
    "sanitize_report_knowledge_citations_for_read",
]
