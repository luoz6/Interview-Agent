from fastapi.testclient import TestClient

import app.api.deletion.routes as deletion_route_module
import app.api.shared.dependencies as api_dependencies
from app.main import app
from app.adapters.memory.context_artifacts import (
    InMemoryContextArtifactStore,
)
from app.services.in_memory_question_memory_index import (
    InMemoryQuestionMemoryIndexStore,
)
from app.services.session_deletion import (
    InMemorySessionDeletionJobStore,
    SessionDeletionService,
)
from app.services.session_deletion_worker import SessionDeletionWorker
from tests.session_fixtures import make_deletion_session_store


def test_deletion_api_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("MEMORY_TRUSTED_LOCAL_DELETION_ENABLED", raising=False)
    client = TestClient(app)

    response = client.delete("/api/interviews/nonexistent")

    assert response.status_code == 404


def test_deletion_api_returns_queued_then_stable_completed_tombstone(monkeypatch):
    monkeypatch.setenv("MEMORY_TRUSTED_LOCAL_DELETION_ENABLED", "true")
    store, session_id = make_deletion_session_store()
    jobs = InMemorySessionDeletionJobStore(job_id_factory=lambda: "delete-job-1")
    service = SessionDeletionService(session_store=store, job_store=jobs)
    worker = SessionDeletionWorker(
        job_store=jobs,
        session_store=store,
        question_memory_index=InMemoryQuestionMemoryIndexStore(),
        context_artifact_store=InMemoryContextArtifactStore(),
    )
    app.dependency_overrides[api_dependencies.get_session_store] = lambda: store
    monkeypatch.setattr(
        deletion_route_module,
        "get_session_deletion_service",
        lambda: service,
    )
    monkeypatch.setattr(
        deletion_route_module,
        "get_session_deletion_worker",
        lambda: worker,
    )
    client = TestClient(app)
    try:
        requested = client.delete(f"/api/interviews/{session_id}")
        status = client.get(f"/api/interviews/{session_id}/deletion")
        repeated = client.delete(f"/api/interviews/{session_id}")
        snapshot = client.get(f"/api/interviews/{session_id}")
    finally:
        app.dependency_overrides.clear()

    assert requested.status_code == 202
    assert requested.json()["status"] == "queued"
    assert "session_id" not in requested.json()
    assert status.json()["status"] == "completed"
    assert repeated.json()["deletion_job_id"] == "delete-job-1"
    assert repeated.json()["status"] == "completed"
    assert snapshot.status_code == 404
