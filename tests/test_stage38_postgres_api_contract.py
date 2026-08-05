import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

import app.api.routes as route_module
from app.main import app
from app.services.postgres_session import PostgresInterviewSessionStore
from app.services.interview_plan_revision_store import InMemoryInterviewPlanRevisionStore
from app.services.prep import InterviewQuestion
from app.services.report_jobs import PostgresReportJobStore
from app.services.runtime import reset_runtime_for_tests
from scripts.stage38_postgres_runtime_acceptance import drop_isolated_tables
from tests.stage38_fakes import FakeStage38InterviewLLM


pytestmark = pytest.mark.pg_runtime


def require_dsn() -> str:
    dsn = os.getenv("POSTGRES_DSN")
    if not dsn:
        pytest.skip("POSTGRES_DSN is required for Stage 38 Postgres API tests")
    return dsn


class FakePublisher:
    def __init__(self):
        self.events = []

    def publish(self, event):
        self.events.append(event)


@pytest.fixture
def postgres_api_client(monkeypatch):
    reset_runtime_for_tests()
    dsn = require_dsn()
    table_prefix = "stage38_api_" + uuid4().hex[:10]
    llm = FakeStage38InterviewLLM()
    store = PostgresInterviewSessionStore(
        dsn=dsn,
        table_prefix=table_prefix,
        llm=llm,
    )
    job_store = PostgresReportJobStore(
        dsn=dsn,
        table_prefix=table_prefix,
        lease_seconds=300,
    )
    publisher = FakePublisher()
    revision_store = InMemoryInterviewPlanRevisionStore()
    def prepare_v2_compatible(job_description, resume_text, **_kwargs):
        plan = llm.generate_plan(job_description, resume_text)
        if len(plan.questions) < 3:
            plan.questions.append(
                InterviewQuestion(
                    id="q3",
                    kind="system-design",
                    prompt="Design a reliable backend service.",
                    focus="reliability",
                )
            )
        return plan
    monkeypatch.setattr(route_module, "get_report_job_store", lambda: job_store)
    monkeypatch.setattr(
        route_module,
        "prepare_interview",
        prepare_v2_compatible,
    )
    app.dependency_overrides[route_module.get_session_store] = lambda: store
    app.dependency_overrides[route_module.get_event_publisher] = lambda: publisher
    app.dependency_overrides[route_module.get_plan_revision_store] = lambda: revision_store
    try:
        yield TestClient(app), store, job_store, publisher
    finally:
        app.dependency_overrides.clear()
        reset_runtime_for_tests()
        drop_isolated_tables(dsn=dsn, table_prefix=table_prefix)


def start_versioned_interview(client):
    prepared = client.post(
        "/api/prep",
        json={
            "job_description": "Backend role with FastAPI, Redis, and PostgreSQL.",
            "resume_text": "Built FastAPI services with Redis cache-aside.",
        },
    )
    assert prepared.status_code == 200
    revision = prepared.json()
    return client.post(
        "/api/interviews",
        json={
            "plan_revision_id": revision["plan_revision_id"],
            "expected_revision": revision["revision"],
            "plan_sha256": revision["plan_sha256"],
        },
    ).json()


def test_stage38_postgres_api_versioned_stream_contract(postgres_api_client):
    client, store, _job_store, publisher = postgres_api_client
    started = start_versioned_interview(client)
    session_id = started["session_id"]

    stale = client.post(
        f"/api/interviews/{session_id}/answer",
        json={
            "answer": "I used Redis.",
            "expected_version": 0,
            "command_id": "cmd-stale",
        },
    )

    assert stale.status_code == 409
    assert stale.json()["actual_version"] == 1

    with client.stream(
        "POST",
        f"/api/interviews/{session_id}/answer/stream",
        json={
            "answer": "I protected PostgreSQL during cache misses.",
            "expected_version": 1,
            "command_id": "cmd-stream",
        },
    ) as response:
        assert response.status_code == 200
        body = "".join(response.iter_text())

    snapshot = client.get(f"/api/interviews/{session_id}").json()
    recovered = PostgresInterviewSessionStore(
        dsn=store.dsn,
        table_prefix=store.table_prefix,
        llm=FakeStage38InterviewLLM(),
    ).snapshot(session_id)

    assert "event: done" in body
    assert snapshot["state_version"] == 3
    assert snapshot["checkpoint_version"] == 3
    assert snapshot["last_command_id"] == "cmd-stream"
    assert recovered["state_version"] == 3
    assert recovered["last_command_id"] == "cmd-stream"
    assert len([m for m in snapshot["messages"] if m["role"] == "candidate"]) == 1
    assert publisher.events == []


def test_stage38_postgres_api_finish_preserves_command_id_through_report_processing(
    postgres_api_client,
):
    client, store, job_store, _publisher = postgres_api_client
    started = start_versioned_interview(client)
    session_id = started["session_id"]

    finish = client.post(
        f"/api/interviews/{session_id}/finish",
        json={
            "expected_version": 1,
            "command_id": "cmd-finish",
        },
    )
    assert finish.status_code == 200

    snapshot = store.snapshot(session_id)
    assert snapshot["status"] == "finished"
    assert snapshot["phase"] == "review"
    assert snapshot["last_command_id"] == "cmd-finish"
    assert snapshot["state_version"] >= 2
    assert job_store.get_job_by_session(session_id) is not None
