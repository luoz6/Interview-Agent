from datetime import datetime, timezone

from app.adapters.memory.user_documents import (
    InMemoryUserDocumentChunkRepository,
    InMemoryUserDocumentStore,
)
from app.application.materials.deletion_service import UserDocumentDeletionService
from app.application.materials.ingestion_service import UserDocumentIngestionService
from app.domain.knowledge.evidence import EvidenceRef
from app.domain.knowledge.source_scope import build_knowledge_source_scope
from app.services.interview_knowledge_scope import InterviewKnowledgeScopeResolver
from app.services.knowledge_citations import project_safe_knowledge_citations
from app.services.report import DimensionScores, InterviewFeedback
from tests.vector_store_fixtures import FakeEmbeddingProvider


NOW = datetime(2026, 8, 15, tzinfo=timezone.utc)
OWNER = "principal-a"


def test_local_material_selection_consumption_citation_deletion_and_scoring_closure():
    store = InMemoryUserDocumentStore()
    chunks = InMemoryUserDocumentChunkRepository()
    ingestion = UserDocumentIngestionService(
        store=store,
        chunks=chunks,
        embedder=FakeEmbeddingProvider(),
        clock=lambda: NOW,
    )
    deletion = UserDocumentDeletionService(
        store=store,
        chunks=chunks,
        clock=lambda: NOW,
    )

    document = ingestion.ingest(
        owner_principal_id=OWNER,
        original_filename="retry-playbook.md",
        media_type="text/markdown",
        content=b"# Retry playbook\n\nUse bounded retries and idempotency keys.",
    )
    snapshot = InterviewKnowledgeScopeResolver(
        store=store,
        clock=lambda: NOW,
    ).resolve(
        owner_principal_id=OWNER,
        selected_document_ids=(document.document_id,),
        include_system_knowledge=False,
    )
    source_scope = build_knowledge_source_scope(
        snapshot,
        owner_principal_id=OWNER,
        usage="feedback",
    )
    selected = snapshot.selected_documents[0]
    evidence = EvidenceRef(
        evidence_id="consumed-user-evidence",
        title="Internal material title",
        safe_excerpt="Use bounded retries and idempotency keys.",
        domain="user_material",
        source_type="user_material",
        content_sha256="e" * 64,
        provenance={
            "knowledge_source": "user_material",
            "document_id": selected.document_id,
            "document_revision_id": selected.document_revision_id,
            "document_content_sha256": selected.content_sha256,
        },
    )

    selected_but_unused = project_safe_knowledge_citations(
        source_scope=source_scope,
        evidence_refs=(evidence,),
        business_binding_evidence_ids=(evidence.evidence_id,),
        final_evidence_ids=(evidence.evidence_id,),
        consumed_evidence_ids=(),
        documents_by_id={document.document_id: document},
    )
    assert selected_but_unused == ()

    citations = project_safe_knowledge_citations(
        source_scope=source_scope,
        evidence_refs=(evidence,),
        business_binding_evidence_ids=(evidence.evidence_id,),
        final_evidence_ids=(evidence.evidence_id,),
        consumed_evidence_ids=(evidence.evidence_id,),
        documents_by_id={document.document_id: document},
    )
    assert len(citations) == 1
    assert citations[0].availability == "available"
    assert citations[0].display_title == "retry-playbook"

    score_fields = DimensionScores(
        breadth=82,
        depth=82,
        architecture=82,
        engineering=82,
        communication=82,
    )
    feedback = InterviewFeedback(
        question_id="q1",
        question_text="How do you make retries safe?",
        user_answer="Use bounded retries and idempotency keys.",
        score=82,
        dimension_scores=score_fields,
        rationale="The candidate answered the question.",
        critique="Add monitoring details.",
        better_answer="Also monitor retry volume and terminal failures.",
        references=[],
        knowledge_citations=list(citations),
    )
    score_snapshot = (feedback.score, feedback.dimension_scores.model_dump())

    result = deletion.delete(
        owner_principal_id=OWNER,
        document_id=document.document_id,
    )
    assert result.deleted_chunks > 0
    assert chunks.list_revision_chunks(
        owner_principal_id=OWNER,
        document_revision_id=selected.document_revision_id,
    ) == ()
    deleted_citations = project_safe_knowledge_citations(
        source_scope=source_scope,
        evidence_refs=(evidence,),
        business_binding_evidence_ids=(evidence.evidence_id,),
        final_evidence_ids=(evidence.evidence_id,),
        consumed_evidence_ids=(evidence.evidence_id,),
        documents_by_id={},
    )
    assert len(deleted_citations) == 1
    assert deleted_citations[0].model_dump(mode="json") | {
        "citation_id": "ignored"
    } == {
        "citation_id": "ignored",
        "source_scope": "user_document",
        "document_safe_ref": None,
        "display_title": "已删除资料",
        "location_label": None,
        "excerpt": None,
        "usage": "feedback",
        "availability": "deleted",
    }

    redacted_feedback = feedback.model_copy(
        update={"knowledge_citations": list(deleted_citations)}
    )
    assert (
        redacted_feedback.score,
        redacted_feedback.dimension_scores.model_dump(),
    ) == score_snapshot
