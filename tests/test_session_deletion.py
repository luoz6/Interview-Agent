from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import app.api.routes as route_module
from app.main import app
from app.services.in_memory_context_artifact_store import (
    InMemoryContextArtifactStore,
)
from app.services.in_memory_question_memory_index import (
    InMemoryQuestionMemoryIndexStore,
)
from app.services.in_memory_principal_memory_control import (
    InMemoryPrincipalMemoryControlStore,
)
from app.services.prep import InterviewPlan, InterviewQuestion
from app.services.session import InterviewSessionStore
from app.services.session_deletion import (
    InMemorySessionDeletionJobStore,
    SessionDeletionService,
)
from app.services.session_deletion_worker import SessionDeletionWorker


class Clock:
    def __init__(self):
        self.value = datetime(2026, 7, 30, tzinfo=timezone.utc)

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += timedelta(seconds=seconds)


class NoopLLM:
    def generate_followup(self, context):
        return "follow-up"


def make_session_store():
    store = InterviewSessionStore(llm=NoopLLM())
    turn = store.start(
        InterviewPlan(
            title="Deletion test",
            questions=[
                InterviewQuestion(
                    id="q1",
                    kind="technical",
                    prompt="Explain retries.",
                    focus="reliability",
                )
            ],
        ),
        job_description="Backend role",
        resume_text="Built services",
        job_tags=["reliability"],
    )
    return store, turn.session_id


def test_deletion_request_is_idempotent_and_revokes_session_immediately():
    store, session_id = make_session_store()
    jobs = InMemorySessionDeletionJobStore(job_id_factory=lambda: "delete-job-1")
    service = SessionDeletionService(session_store=store, job_store=jobs)

    first = service.request(session_id)
    second = service.request(session_id)

    assert first.job_id == second.job_id
    assert store.get(session_id)["deletion_status"] == "deleting"


def test_stale_deletion_worker_cannot_complete_after_lease_reclaim():
    clock = Clock()
    jobs = InMemorySessionDeletionJobStore(
        clock=clock,
        job_id_factory=lambda: "delete-job-1",
    )
    jobs.request("session-1")
    stale = jobs.claim(worker_id="worker-a", lease_seconds=5)
    clock.advance(6)
    current = jobs.claim(worker_id="worker-b", lease_seconds=5)

    with pytest.raises(RuntimeError, match="lease was lost"):
        jobs.complete(stale, safe_counts={})

    completed = jobs.complete(current, safe_counts={"business_sessions": 1})
    assert completed.status == "completed"
    assert completed.fencing_version == 2


def test_worker_purge_is_replay_safe_and_returns_only_safe_counts():
    store, session_id = make_session_store()
    jobs = InMemorySessionDeletionJobStore(job_id_factory=lambda: "delete-job-1")
    service = SessionDeletionService(session_store=store, job_store=jobs)
    service.request(session_id)
    worker = SessionDeletionWorker(
        job_store=jobs,
        session_store=store,
        question_memory_index=InMemoryQuestionMemoryIndexStore(),
        context_artifact_store=InMemoryContextArtifactStore(),
    )

    completed = worker.run_once()

    assert completed.status == "completed"
    assert completed.safe_counts == {
        "workflow_rows": 0,
        "question_memory_rows": 0,
        "artifact_owner_refs": 0,
        "report_history_rows": 0,
        "failure_state_rows": 0,
        "principal_memory_rows": 0,
        "principal_memory_control_rows": 0,
        "business_sessions": 1,
    }
    assert worker.run_once() is None
    with pytest.raises(ValueError, match="session not found"):
        store.get(session_id)


def test_worker_purges_durable_principal_memory_session_control():
    store, session_id = make_session_store()
    jobs = InMemorySessionDeletionJobStore(job_id_factory=lambda: "delete-job-control")
    SessionDeletionService(session_store=store, job_store=jobs).request(session_id)
    controls = InMemoryPrincipalMemoryControlStore()
    controls.set_session(
        deployment_id="single-tenant-local",
        principal_id="local-owner",
        session_id=session_id,
        enabled=False,
        updated_at=datetime(2026, 8, 4, tzinfo=timezone.utc),
    )
    worker = SessionDeletionWorker(
        job_store=jobs,
        session_store=store,
        principal_memory_control_store=controls,
    )

    completed = worker.run_once()

    assert completed.safe_counts["principal_memory_control_rows"] == 1
    assert controls.get_session(
        deployment_id="single-tenant-local",
        principal_id="local-owner",
        session_id=session_id,
    ) is None


def test_deletion_api_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("MEMORY_TRUSTED_LOCAL_DELETION_ENABLED", raising=False)
    client = TestClient(app)

    response = client.delete("/api/interviews/nonexistent")

    assert response.status_code == 404


def test_deletion_api_returns_queued_then_stable_completed_tombstone(monkeypatch):
    monkeypatch.setenv("MEMORY_TRUSTED_LOCAL_DELETION_ENABLED", "true")
    store, session_id = make_session_store()
    jobs = InMemorySessionDeletionJobStore(job_id_factory=lambda: "delete-job-1")
    service = SessionDeletionService(session_store=store, job_store=jobs)
    worker = SessionDeletionWorker(
        job_store=jobs,
        session_store=store,
        question_memory_index=InMemoryQuestionMemoryIndexStore(),
        context_artifact_store=InMemoryContextArtifactStore(),
    )
    app.dependency_overrides[route_module.get_session_store] = lambda: store
    monkeypatch.setattr(route_module, "get_session_deletion_service", lambda: service)
    monkeypatch.setattr(route_module, "get_session_deletion_worker", lambda: worker)
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
