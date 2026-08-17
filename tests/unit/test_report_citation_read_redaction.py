from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import uuid4

from fastapi.testclient import TestClient

import app.api.reports.routes as report_routes
from app.adapters.memory.user_documents import InMemoryUserDocumentStore
from app.domain.knowledge.evidence import EvidenceRef
from app.domain.knowledge.source_scope import (
    SelectedUserDocumentRevision,
    build_knowledge_source_scope,
)
from app.domain.knowledge.user_document import (
    UserDocument,
    UserDocumentPublicStatus,
    UserDocumentRevision,
)
from app.graphs.interview_state import build_initial_state
from app.main import app
from app.services.interview_plan_revision import (
    InterviewPlanQuestionV2,
    InterviewPlanV2,
    build_interview_knowledge_scope_snapshot,
    default_plan_configuration,
    plan_payload_sha256,
    v2_plan_to_legacy,
)
from app.services.knowledge_citations import (
    project_safe_knowledge_citations,
    sanitize_report_knowledge_citations_for_read,
)
from app.services.report import (
    DimensionScores,
    InterviewFeedback,
    InterviewReport,
    ReportRecord,
)
from app.services.report_artifact import PublishReportArtifact
from app.services.report_artifact_store import InMemoryReportArtifactStore
from app.services.session_plan_binding import SessionPlanBinding


NOW = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)
SESSION_ID = "citation-read-session"
OWNER = "principal-a"
DOCUMENT_ID = str(uuid4())
REVISION_ID = str(uuid4())
DOCUMENT_SHA256 = "d" * 64
EVIDENCE_ID = "internal-user-evidence"
PRIVATE_TITLE = "Private interview playbook"
PRIVATE_EXCERPT = "Use bounded retries and idempotency keys."


class SessionReportStore:
    def __init__(self, state, report):
        self.state = state
        self.report = report

    def get(self, session_id):
        if session_id != SESSION_ID:
            raise ValueError("session not found")
        return self.state

    def get_report_record(self, session_id):
        self.get(session_id)
        return ReportRecord(status="completed", report=self.report)

    def list_question_evaluations(self, session_id):
        self.get(session_id)
        return []


def _document_store():
    store = InMemoryUserDocumentStore()
    document = UserDocument(
        document_id=DOCUMENT_ID,
        owner_principal_id=OWNER,
        display_title=PRIVATE_TITLE,
        original_filename="private-interview-playbook.md",
        media_type="text/markdown",
        size_bytes=128,
        public_status=UserDocumentPublicStatus.READY,
        enabled=True,
        active_revision_id=REVISION_ID,
        created_at=NOW,
        updated_at=NOW,
    )
    revision = UserDocumentRevision(
        document_revision_id=REVISION_ID,
        document_id=DOCUMENT_ID,
        revision=1,
        original_file_sha256="a" * 64,
        content_sha256=DOCUMENT_SHA256,
        extracted_text_ref=f"memory:user-material:{REVISION_ID}",
        parser_version="utf8-text-v1",
        chunker_version="paragraph-v1",
        embedding_identity="fake:test:v1:2",
        created_at=NOW,
    )
    store.create_document(owner_principal_id=OWNER, document=document)
    store.create_revision(
        owner_principal_id=OWNER,
        revision=revision,
        original_content=b"private interview playbook",
        extracted_text=PRIVATE_EXCERPT,
    )
    return store, document


def _scope_snapshot():
    return build_interview_knowledge_scope_snapshot(
        include_system_knowledge=False,
        selected_documents=(
            SelectedUserDocumentRevision(
                document_id=DOCUMENT_ID,
                document_revision_id=REVISION_ID,
                content_sha256=DOCUMENT_SHA256,
                allowed_usages=("feedback",),
            ),
        ),
        created_at=NOW,
    )


def _session_state(scope_snapshot):
    question_id = str(uuid4())
    plan = InterviewPlanV2(
        title="Backend interview",
        configuration_snapshot=default_plan_configuration(),
        knowledge_scope=scope_snapshot,
        questions=(
            InterviewPlanQuestionV2(
                question_id=question_id,
                position=1,
                question_text="Explain an idempotent retry strategy.",
                focus="Reliability",
                question_type="technical",
                difficulty="intermediate",
                expected_minutes=8,
                expected_followups=0,
                origin="generated",
            ),
        ),
    )
    binding = SessionPlanBinding(
        plan_origin="plan_revision",
        plan_revision_id=str(uuid4()),
        plan_family_id=str(uuid4()),
        revision=1,
        plan_sha256=plan_payload_sha256(plan),
        configuration_snapshot=plan.configuration_snapshot.model_dump(mode="json"),
        plan_snapshot=plan.model_dump(mode="json"),
        owner_principal_id=OWNER,
    )
    state = build_initial_state(
        session_id=SESSION_ID,
        plan=v2_plan_to_legacy(plan),
        job_description="Backend reliability role",
        resume_text="Built reliable APIs",
        job_tags=["reliability"],
        plan_binding=binding,
    )
    state["status"] = "finished"
    state["current_index"] = 1
    return state, question_id


def _report(scope_snapshot, document, question_id):
    source_scope = build_knowledge_source_scope(
        scope_snapshot,
        owner_principal_id=OWNER,
        usage="feedback",
    )
    reference = EvidenceRef(
        evidence_id=EVIDENCE_ID,
        title="Internal title",
        safe_excerpt=PRIVATE_EXCERPT,
        domain="user_material",
        source_type="user_material",
        content_sha256="c" * 64,
        provenance={
            "knowledge_source": "user_material",
            "document_id": DOCUMENT_ID,
            "document_revision_id": REVISION_ID,
            "document_content_sha256": DOCUMENT_SHA256,
        },
    )
    citation = project_safe_knowledge_citations(
        source_scope=source_scope,
        evidence_refs=(reference,),
        business_binding_evidence_ids=(EVIDENCE_ID,),
        final_evidence_ids=(EVIDENCE_ID,),
        consumed_evidence_ids=(EVIDENCE_ID,),
        documents_by_id={DOCUMENT_ID: document},
    )[0]
    assert citation.availability == "available"
    assert citation.display_title == PRIVATE_TITLE
    assert citation.excerpt == PRIVATE_EXCERPT
    return InterviewReport(
        session_id=SESSION_ID,
        overall_score=70,
        overall_dimension_scores=DimensionScores(
            breadth=70,
            depth=70,
            architecture=70,
            engineering=70,
            communication=70,
        ),
        summary="Stable control report.",
        highlights=["Explained retry bounds."],
        feedbacks=[
            InterviewFeedback(
                question_id=question_id,
                question_text="Explain an idempotent retry strategy.",
                user_answer="I use idempotency keys and bounded retries.",
                score=70,
                dimension_scores=DimensionScores(
                    breadth=70,
                    depth=70,
                    architecture=70,
                    engineering=70,
                    communication=70,
                ),
                rationale="The candidate described the core mechanism.",
                critique="Monitoring details were limited.",
                better_answer="Add retry metrics and alert thresholds.",
                references=[],
                knowledge_citations=[citation],
            )
        ],
    )


def _publish_artifact(artifact_store, report):
    job = artifact_store.enqueue_job(
        session_id=SESSION_ID,
        idempotency_key="initial-citation-report",
    )
    job = artifact_store.claim_job(job.job_id, worker_id="worker-1")
    return artifact_store.publish(
        job.job_id,
        PublishReportArtifact(
            schema_version="report-artifact-v2",
            scoring_rubric_version=report.scoring_rubric_version,
            generation_status=report.generation_status,
            generation_reason_code=report.generation_reason_code,
            score_status=report.score_status,
            score_reason_code=report.score_reason_code,
            coverage_status=report.coverage_status,
            report_path=report.report_path,
            payload=report.model_dump(mode="json"),
        ),
        worker_id="worker-1",
    )


def _assert_deleted_public_citation(payload):
    citation = payload["feedbacks"][0]["knowledge_citations"][0]
    assert citation == {
        "citation_id": citation["citation_id"],
        "source_scope": "user_document",
        "document_safe_ref": None,
        "display_title": "已删除资料",
        "location_label": None,
        "excerpt": None,
        "usage": "feedback",
        "availability": "deleted",
    }
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    assert PRIVATE_TITLE not in serialized
    assert PRIVATE_EXCERPT not in serialized
    assert "material-" not in serialized
    assert "knowledge_citations" not in {
        key for key in payload if key != "feedbacks"
    }


def test_deleted_material_is_redacted_from_all_public_report_reads(monkeypatch):
    documents, document = _document_store()
    scope_snapshot = _scope_snapshot()
    state, question_id = _session_state(scope_snapshot)
    report = _report(scope_snapshot, document, question_id)
    session_store = SessionReportStore(state, report)
    artifact_store = InMemoryReportArtifactStore()
    artifact = _publish_artifact(artifact_store, report)
    persisted_artifact = artifact_store.get_artifact(artifact.report_id)
    captured_pdf_reports = []

    def capture_pdf(public_report, **_identity):
        captured_pdf_reports.append(public_report.model_copy(deep=True))
        return b"%PDF-redacted"

    monkeypatch.setattr(report_routes, "build_report_pdf", capture_pdf)
    app.dependency_overrides[report_routes.get_session_store] = lambda: session_store
    app.dependency_overrides[report_routes.get_report_artifact_store] = (
        lambda: artifact_store
    )
    app.dependency_overrides[report_routes.get_user_document_store] = (
        lambda: documents
    )
    client = TestClient(app)
    try:
        monkeypatch.setattr(
            report_routes,
            "get_report_artifact_read_mode",
            lambda: "legacy_first",
        )
        before_delete = client.get(f"/api/interviews/{SESSION_ID}/report")
        assert before_delete.status_code == 200
        assert before_delete.json()["feedbacks"][0]["knowledge_citations"][0][
            "availability"
        ] == "available"

        documents.delete_document(
            owner_principal_id=OWNER,
            document_id=DOCUMENT_ID,
        )

        legacy_json = client.get(f"/api/interviews/{SESSION_ID}/report")
        legacy_pdf = client.get(f"/api/interviews/{SESSION_ID}/report.pdf")
        assert legacy_json.status_code == 200
        assert legacy_pdf.status_code == 200
        _assert_deleted_public_citation(legacy_json.json())

        monkeypatch.setattr(
            report_routes,
            "get_report_artifact_read_mode",
            lambda: "artifact_first",
        )
        active_json = client.get(f"/api/interviews/{SESSION_ID}/report")
        artifact_list = client.get(f"/api/interviews/{SESSION_ID}/reports")
        artifact_detail = client.get(
            f"/api/reports/{artifact.report_id}",
            params={"session_id": SESSION_ID},
        )
        active_pdf = client.get(f"/api/interviews/{SESSION_ID}/report.pdf")
        artifact_pdf = client.get(
            f"/api/reports/{artifact.report_id}.pdf",
            params={"session_id": SESSION_ID},
        )

        assert active_json.status_code == 200
        assert artifact_list.status_code == 200
        assert artifact_detail.status_code == 200
        assert active_pdf.status_code == 200
        assert artifact_pdf.status_code == 200
        _assert_deleted_public_citation(
            active_json.json()["active_artifact"]["payload"]
        )
        _assert_deleted_public_citation(
            artifact_list.json()["items"][0]["payload"]
        )
        _assert_deleted_public_citation(artifact_detail.json()["payload"])
        assert len(captured_pdf_reports) == 3
        for captured in captured_pdf_reports:
            _assert_deleted_public_citation(captured.model_dump(mode="json"))

        # Read-time redaction must not rewrite either persistence representation.
        assert session_store.report.feedbacks[0].knowledge_citations[0].availability == (
            "available"
        )
        assert artifact_store.get_artifact(artifact.report_id) == persisted_artifact
        persisted_citation = persisted_artifact.payload["feedbacks"][0][
            "knowledge_citations"
        ][0]
        assert persisted_citation["display_title"] == PRIVATE_TITLE
        assert persisted_citation["excerpt"] == PRIVATE_EXCERPT
        assert persisted_citation["document_safe_ref"].startswith("material-")
    finally:
        app.dependency_overrides.clear()


def test_cross_owner_and_store_lookup_failure_both_fail_closed():
    documents, document = _document_store()
    scope_snapshot = _scope_snapshot()
    state, question_id = _session_state(scope_snapshot)
    report = _report(scope_snapshot, document, question_id)

    class CrossOwnerStore:
        def get_document(self, **_kwargs):
            return document.model_copy(
                update={"owner_principal_id": "principal-b"}
            )

        def get_revision(self, **kwargs):
            return documents.get_revision(**kwargs)

    class FailingStore:
        def get_document(self, **_kwargs):
            raise RuntimeError("document store unavailable")

        def get_revision(self, **_kwargs):
            raise AssertionError("lookup must stop after the first failure")

    for store in (CrossOwnerStore(), FailingStore(), None):
        sanitized = sanitize_report_knowledge_citations_for_read(
            report,
            session_state=state,
            user_document_store=store,
        )
        _assert_deleted_public_citation(sanitized.model_dump(mode="json"))


def test_report_without_user_citations_stays_readable_when_store_is_unavailable(
    monkeypatch,
):
    documents, document = _document_store()
    scope_snapshot = _scope_snapshot()
    state, question_id = _session_state(scope_snapshot)
    report = _report(scope_snapshot, document, question_id)
    feedback = report.feedbacks[0].model_copy(update={"knowledge_citations": []})
    report = report.model_copy(update={"feedbacks": [feedback]})
    session_store = SessionReportStore(state, report)

    def unavailable_store():
        raise RuntimeError("document store unavailable")

    def unavailable_artifact_store():
        raise RuntimeError("artifact store unavailable")

    monkeypatch.setattr(
        report_routes,
        "get_report_artifact_read_mode",
        lambda: "legacy_first",
    )
    app.dependency_overrides[report_routes.get_session_store] = lambda: session_store
    app.dependency_overrides[report_routes.get_report_artifact_store] = (
        unavailable_artifact_store
    )
    app.dependency_overrides[report_routes.get_user_document_store] = unavailable_store
    try:
        response = TestClient(app).get(f"/api/interviews/{SESSION_ID}/report")

        assert response.status_code == 200
        assert response.json()["feedbacks"][0]["knowledge_citations"] == []
        assert response.json()["summary"] == "Stable control report."
    finally:
        app.dependency_overrides.clear()
