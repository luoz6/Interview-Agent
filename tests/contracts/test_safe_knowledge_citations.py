from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.domain.knowledge.evidence import EvidenceRef, SafeKnowledgeCitation
from app.domain.knowledge.source_scope import (
    SelectedUserDocumentRevision,
    build_knowledge_source_scope,
)
from app.domain.knowledge.user_document import (
    UserDocument,
    UserDocumentPublicStatus,
)
from app.services.interview_plan_revision import (
    build_interview_knowledge_scope_snapshot,
)
from app.services.knowledge_citations import project_safe_knowledge_citations


NOW = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)
OWNER_A = "principal-a"
OWNER_B = "principal-b"
DOCUMENT_ID = str(uuid4())
REVISION_ID = str(uuid4())
DOCUMENT_SHA256 = "d" * 64
USER_EVIDENCE_ID = "internal-user-chunk-id"
SYSTEM_EVIDENCE_ID = "internal-system-chunk-id"


def _scope(*, include_system: bool = True, usage: str = "feedback"):
    snapshot = build_interview_knowledge_scope_snapshot(
        include_system_knowledge=include_system,
        selected_documents=(
            SelectedUserDocumentRevision(
                document_id=DOCUMENT_ID,
                document_revision_id=REVISION_ID,
                content_sha256=DOCUMENT_SHA256,
                allowed_usages=("question", "follow_up", "feedback"),
            ),
        ),
        created_at=NOW,
    )
    return build_knowledge_source_scope(
        snapshot,
        owner_principal_id=OWNER_A,
        usage=usage,
    )


def _user_reference(
    *,
    evidence_id: str = USER_EVIDENCE_ID,
    document_id: str = DOCUMENT_ID,
    revision_id: str = REVISION_ID,
    document_sha256: str = DOCUMENT_SHA256,
    excerpt: str = "Use bounded retries and idempotency keys.",
) -> EvidenceRef:
    return EvidenceRef(
        evidence_id=evidence_id,
        title="Internal chunk title",
        safe_excerpt=excerpt,
        domain="user_material",
        source_type="user_material",
        content_sha256="c" * 64,
        provenance={
            "knowledge_source": "user_material",
            "document_id": document_id,
            "document_revision_id": revision_id,
            "document_content_sha256": document_sha256,
        },
    )


def _system_reference() -> EvidenceRef:
    return EvidenceRef(
        evidence_id=SYSTEM_EVIDENCE_ID,
        title="System reliability guide",
        safe_excerpt="Prefer idempotent consumers.",
        domain="distributed-systems",
        source_type="engineering_guide",
        content_sha256="a" * 64,
        corpus_manifest_sha256="b" * 64,
        corpus_version="v1",
    )


def _document(
    *,
    owner: str = OWNER_A,
    document_id: str = DOCUMENT_ID,
    active_revision_id: str = REVISION_ID,
    title: str = "My project notes",
) -> UserDocument:
    return UserDocument(
        document_id=document_id,
        owner_principal_id=owner,
        display_title=title,
        original_filename="private-system-design.md",
        media_type="text/markdown",
        size_bytes=256,
        public_status=UserDocumentPublicStatus.READY,
        enabled=True,
        active_revision_id=active_revision_id,
        created_at=NOW,
        updated_at=NOW,
    )


def _project(
    *,
    references=None,
    binding_ids=(USER_EVIDENCE_ID,),
    final_ids=(USER_EVIDENCE_ID,),
    consumed_ids=(USER_EVIDENCE_ID,),
    documents=None,
    include_system=True,
):
    return project_safe_knowledge_citations(
        source_scope=_scope(include_system=include_system),
        evidence_refs=references or (_user_reference(),),
        business_binding_evidence_ids=binding_ids,
        final_evidence_ids=final_ids,
        consumed_evidence_ids=consumed_ids,
        documents_by_id=(
            {DOCUMENT_ID: _document()} if documents is None else documents
        ),
    )


@pytest.mark.parametrize(
    ("binding_ids", "final_ids", "consumed_ids"),
    [
        ((), (USER_EVIDENCE_ID,), (USER_EVIDENCE_ID,)),
        ((USER_EVIDENCE_ID,), (), (USER_EVIDENCE_ID,)),
        ((USER_EVIDENCE_ID,), (USER_EVIDENCE_ID,), ()),
    ],
)
def test_citation_requires_business_binding_final_evidence_and_actual_consumption(
    binding_ids,
    final_ids,
    consumed_ids,
):
    assert _project(
        binding_ids=binding_ids,
        final_ids=final_ids,
        consumed_ids=consumed_ids,
    ) == ()


def test_only_full_intersection_projects_a_citation_and_bounds_excerpt():
    citations = _project(
        references=(_user_reference(excerpt="private\n" + "x" * 700),),
    )

    assert len(citations) == 1
    citation = citations[0]
    assert citation.source_scope == "user_document"
    assert citation.usage == "feedback"
    assert citation.display_title == "My project notes"
    assert citation.document_safe_ref.startswith("material-")
    assert citation.availability == "available"
    assert citation.excerpt is not None
    assert "\n" not in citation.excerpt
    assert len(citation.excerpt) == 500


def test_selected_but_unused_evidence_never_becomes_referenced():
    assert _project(consumed_ids=()) == ()


def test_out_of_scope_user_lineage_cannot_be_projected():
    other_document_id = str(uuid4())
    other_revision_id = str(uuid4())
    out_of_scope = _user_reference(
        document_id=other_document_id,
        revision_id=other_revision_id,
        document_sha256="e" * 64,
    )

    assert _project(
        references=(out_of_scope,),
        documents={
            other_document_id: _document(
                document_id=other_document_id,
                active_revision_id=other_revision_id,
                title="Secret document from outside the frozen scope",
            )
        },
    ) == ()


def test_missing_or_other_owner_document_collapses_to_deleted_projection():
    missing = _project(documents={})[0]
    other_owner = _project(
        documents={
            DOCUMENT_ID: _document(
                owner=OWNER_B,
                title="Other owner's confidential title",
            )
        }
    )[0]

    for citation in (missing, other_owner):
        assert citation.source_scope == "user_document"
        assert citation.availability == "deleted"
        assert citation.display_title == "已删除资料"
        assert citation.excerpt is None
        assert citation.document_safe_ref is None
        assert citation.location_label is None
    assert "confidential" not in json.dumps(
        other_owner.model_dump(mode="json"),
        ensure_ascii=False,
    )


def test_revision_that_is_no_longer_accessible_uses_unavailable_projection():
    citation = _project(
        documents={
            DOCUMENT_ID: _document(active_revision_id=str(uuid4()))
        }
    )[0]

    assert citation.availability == "unavailable"
    assert citation.display_title == "资料暂不可用"
    assert citation.excerpt is None


def test_user_and_system_citations_group_by_safe_source_scope():
    citations = _project(
        references=(_user_reference(), _system_reference()),
        binding_ids=(USER_EVIDENCE_ID, SYSTEM_EVIDENCE_ID),
        final_ids=(USER_EVIDENCE_ID, SYSTEM_EVIDENCE_ID),
        consumed_ids=(USER_EVIDENCE_ID, SYSTEM_EVIDENCE_ID),
    )

    assert [citation.source_scope for citation in citations] == [
        "user_document",
        "system_knowledge",
    ]
    assert citations[1].document_safe_ref is None
    assert citations[1].display_title == "System reliability guide"


def test_system_citation_is_rejected_when_system_knowledge_is_out_of_scope():
    assert _project(
        references=(_system_reference(),),
        binding_ids=(SYSTEM_EVIDENCE_ID,),
        final_ids=(SYSTEM_EVIDENCE_ID,),
        consumed_ids=(SYSTEM_EVIDENCE_ID,),
        include_system=False,
    ) == ()


def test_public_citation_schema_forbids_internal_lineage_and_sensitive_values():
    citation = _project()[0]
    payload = citation.model_dump(mode="json")
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)

    assert set(payload) == {
        "citation_id",
        "source_scope",
        "document_safe_ref",
        "display_title",
        "location_label",
        "excerpt",
        "usage",
        "availability",
    }
    for forbidden_value in (
        USER_EVIDENCE_ID,
        DOCUMENT_ID,
        REVISION_ID,
        DOCUMENT_SHA256,
        OWNER_A,
        "private-system-design.md",
    ):
        assert forbidden_value not in serialized
    for forbidden_key in (
        "owner_principal_id",
        "document_revision_id",
        "chunk_id",
        "source_id",
        "content_sha256",
        "manifest",
        "query",
        "prompt",
        "resume",
        "job_description",
        "trace",
        "filename",
        "path",
    ):
        assert forbidden_key not in payload

    with pytest.raises(ValidationError, match="extra_forbidden"):
        SafeKnowledgeCitation.model_validate(
            {
                **payload,
                "chunk_id": USER_EVIDENCE_ID,
            }
        )


def test_deleted_citation_model_cannot_retain_title_excerpt_or_safe_ref():
    payload = _project(documents={})[0].model_dump(mode="json")

    with pytest.raises(ValidationError, match="content-free deleted projection"):
        SafeKnowledgeCitation.model_validate(
            {
                **payload,
                "display_title": "Old private title",
                "excerpt": "Old private excerpt",
                "document_safe_ref": "material-" + "a" * 32,
            }
        )
