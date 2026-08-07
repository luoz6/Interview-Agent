from fastapi.testclient import TestClient

import app.api.routes as routes
from app.main import app
from app.services.report_artifact import PublishReportArtifact
from app.services.report_artifact_store import InMemoryReportArtifactStore
from tests.test_report_pdf import make_report


class FinishedSessionStore:
    def get(self, session_id):
        if session_id != "session-1":
            raise ValueError("session not found")
        return {"session_id": session_id, "status": "finished", "deletion_status": None}


class TrackingArtifactStore(InMemoryReportArtifactStore):
    def __init__(self):
        super().__init__()
        self.latest_job_calls = 0
        self.list_job_calls = 0

    def get_latest_job(self, session_id):
        self.latest_job_calls += 1
        return super().get_latest_job(session_id)

    def list_jobs(self, session_id):
        self.list_job_calls += 1
        return super().list_jobs(session_id)


def payload():
    return PublishReportArtifact(
        schema_version="report-artifact-v2",
        scoring_rubric_version="rubric-v1",
        generation_status="complete",
        generation_reason_code="normal",
        score_status="scored",
        score_reason_code="sufficient_evidence",
        coverage_status="complete",
        report_path="full_session",
        payload={
            "overall_score": 84,
            "overall_dimension_scores": {"depth": 84},
            "evaluated_count": 1,
            "total_eligible_count": 1,
            "evidence_count": 1,
        },
    )


def renderable_payload(summary: str) -> PublishReportArtifact:
    report = make_report().model_copy(
        update={"session_id": "session-1", "summary": summary}
    )
    return PublishReportArtifact(
        schema_version="report-artifact-v2",
        scoring_rubric_version=report.scoring_rubric_version,
        generation_status=report.generation_status,
        generation_reason_code=report.generation_reason_code,
        score_status=report.score_status,
        score_reason_code=report.score_reason_code,
        coverage_status=report.coverage_status,
        report_path=report.report_path,
        payload=report.model_dump(mode="json"),
    )


def test_report_version_endpoints_keep_active_artifact_when_rescore_fails():
    artifacts = InMemoryReportArtifactStore()
    initial = artifacts.enqueue_job(session_id="session-1", idempotency_key="initial")
    initial = artifacts.claim_job(initial.job_id, worker_id="w1")
    first = artifacts.publish(initial.job_id, payload(), worker_id="w1")
    app.dependency_overrides[routes.get_session_store] = lambda: FinishedSessionStore()
    app.dependency_overrides[routes.get_report_artifact_store] = lambda: artifacts
    try:
        client = TestClient(app)
        current = client.get("/api/interviews/session-1/report")
        assert current.status_code == 200
        assert current.json()["active_artifact"]["report_id"] == first.report_id

        queued = client.post(
            "/api/interviews/session-1/report/rescore",
            json={"activate_on_success": True, "idempotency_key": "rescore-1"},
        )
        assert queued.status_code == 202
        job_id = queued.json()["report_job_id"]
        artifacts.fail_job(job_id, error_code="provider_timeout")

        after_failure = client.get("/api/interviews/session-1/report").json()
        assert after_failure["active_artifact"]["report_id"] == first.report_id
        assert after_failure["latest_job"]["status"] == "failed"
        history = client.get("/api/interviews/session-1/report-jobs").json()["items"]
        assert [item["job_kind"] for item in history] == ["initial", "rescore"]
        versions = client.get("/api/interviews/session-1/reports").json()["items"]
        assert versions[0]["active"] is True
        assert client.get(
            f"/api/reports/{first.report_id}",
            params={"session_id": "session-1"},
        ).status_code == 200
    finally:
        app.dependency_overrides.clear()


def test_active_report_get_does_not_materialize_job_history():
    artifacts = TrackingArtifactStore()
    initial = artifacts.enqueue_job(session_id="session-1", idempotency_key="initial")
    initial = artifacts.claim_job(initial.job_id, worker_id="w1")
    active = artifacts.publish(initial.job_id, payload(), worker_id="w1")
    for index in range(19):
        job = artifacts.enqueue_job(
            session_id="session-1",
            job_kind="rescore",
            source_report_id=active.report_id,
            idempotency_key=f"history-{index}",
        )
        artifacts.fail_job(job.job_id, error_code="synthetic_history")
    app.dependency_overrides[routes.get_session_store] = lambda: FinishedSessionStore()
    app.dependency_overrides[routes.get_report_artifact_store] = lambda: artifacts
    try:
        response = TestClient(app).get("/api/interviews/session-1/report")

        assert response.status_code == 200
        assert response.json()["latest_job"]["error_code"] == (
            "report_generation_failed"
        )
        assert artifacts.latest_job_calls == 1
        assert artifacts.list_job_calls == 0
    finally:
        app.dependency_overrides.clear()


def test_failed_initial_job_without_active_report_is_visible_and_requeueable():
    artifacts = InMemoryReportArtifactStore()
    initial = artifacts.enqueue_job(
        session_id="session-1",
        idempotency_key="initial-failed",
    )
    claimed = artifacts.claim_job(initial.job_id, worker_id="w1")
    artifacts.fail_job(
        claimed.job_id,
        error_code="provider_timeout",
    )
    app.dependency_overrides[routes.get_session_store] = lambda: FinishedSessionStore()
    app.dependency_overrides[routes.get_report_artifact_store] = lambda: artifacts
    app.dependency_overrides[routes.get_report_job_queue] = lambda: object()
    try:
        client = TestClient(app)
        failed = client.get("/api/interviews/session-1/report")

        assert failed.status_code == 500
        assert failed.json()["active_artifact"] is None
        assert failed.json()["latest_job"]["status"] == "failed"
        assert failed.json()["latest_job"]["error_code"] == (
            "report_provider_timeout"
        )

        requeued = client.post(
            "/api/interviews/session-1/report/requeue",
            json={},
        )
        assert requeued.status_code == 202
        assert requeued.json()["report_job_id"] == initial.job_id
        assert requeued.json()["status"] == "queued"
        assert requeued.json()["active_report_id"] is None

        processing = client.get("/api/interviews/session-1/report")
        assert processing.status_code == 202
        assert processing.json()["active_artifact"] is None
        assert processing.json()["latest_job"]["status"] == "queued"
    finally:
        app.dependency_overrides.clear()


def test_historical_pdf_is_bound_to_requested_artifact_after_active_pointer_moves(
    monkeypatch,
):
    artifacts = InMemoryReportArtifactStore()
    initial = artifacts.enqueue_job(session_id="session-1", idempotency_key="pdf-v1")
    initial = artifacts.claim_job(initial.job_id, worker_id="w1")
    first = artifacts.publish(
        initial.job_id,
        renderable_payload("immutable summary version one"),
        worker_id="w1",
    )
    rescore = artifacts.enqueue_job(
        session_id="session-1",
        job_kind="rescore",
        source_report_id=first.report_id,
        idempotency_key="pdf-v2",
    )
    rescore = artifacts.claim_job(rescore.job_id, worker_id="w2")
    second = artifacts.publish(
        rescore.job_id,
        renderable_payload("active summary version two"),
        worker_id="w2",
    )
    captures = []

    def capture_pdf(report, **identity):
        captures.append((report.summary, identity))
        return b"%PDF-version-bound"

    monkeypatch.setattr(routes, "build_report_pdf", capture_pdf)
    app.dependency_overrides[routes.get_session_store] = lambda: FinishedSessionStore()
    app.dependency_overrides[routes.get_report_artifact_store] = lambda: artifacts
    try:
        client = TestClient(app)
        historical = client.get(
            f"/api/reports/{first.report_id}.pdf",
            params={"session_id": "session-1"},
        )
        active = client.get("/api/interviews/session-1/report.pdf")

        assert artifacts.get_head("session-1").active_report_id == second.report_id
        assert historical.status_code == 200
        assert f"r1-{first.report_id[:8]}" in historical.headers["content-disposition"]
        assert captures[0][0] == "immutable summary version one"
        assert captures[0][1]["report_id"] == first.report_id
        assert captures[0][1]["revision"] == 1
        assert active.status_code == 200
        assert f"r2-{second.report_id[:8]}" in active.headers["content-disposition"]
        assert captures[1][0] == "active summary version two"
        assert captures[1][1]["report_id"] == second.report_id
        assert captures[1][1]["revision"] == 2
    finally:
        app.dependency_overrides.clear()


def test_pdf_export_failure_does_not_mutate_artifact_or_active_pointer(monkeypatch):
    artifacts = InMemoryReportArtifactStore()
    initial = artifacts.enqueue_job(session_id="session-1", idempotency_key="pdf-fail")
    initial = artifacts.claim_job(initial.job_id, worker_id="w1")
    published = artifacts.publish(
        initial.job_id,
        renderable_payload("immutable before export failure"),
        worker_id="w1",
    )
    before_artifact = artifacts.get_artifact(published.report_id)
    before_head = artifacts.get_head("session-1")
    before_jobs = artifacts.list_jobs("session-1")

    def fail_export(*args, **kwargs):
        raise RuntimeError("synthetic PDF renderer failure")

    monkeypatch.setattr(routes, "build_report_pdf", fail_export)
    app.dependency_overrides[routes.get_session_store] = lambda: FinishedSessionStore()
    app.dependency_overrides[routes.get_report_artifact_store] = lambda: artifacts
    try:
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get(
            f"/api/reports/{published.report_id}.pdf",
            params={"session_id": "session-1"},
        )

        assert response.status_code == 500
        assert artifacts.get_artifact(published.report_id) == before_artifact
        assert artifacts.get_head("session-1") == before_head
        assert artifacts.list_jobs("session-1") == before_jobs
    finally:
        app.dependency_overrides.clear()
