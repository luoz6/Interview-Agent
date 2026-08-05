from fastapi.testclient import TestClient

import app.api.routes as routes
from app.main import app
from app.services.report_artifact import PublishReportArtifact
from app.services.report_artifact_store import InMemoryReportArtifactStore


class FinishedSessionStore:
    def get(self, session_id):
        if session_id != "session-1":
            raise ValueError("session not found")
        return {"session_id": session_id, "status": "finished", "deletion_status": None}


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
        assert client.get(f"/api/reports/{first.report_id}").status_code == 200
    finally:
        app.dependency_overrides.clear()
