from __future__ import annotations

from fastapi.testclient import TestClient

import app.api.shared.dependencies as api_dependencies
from app.api.shared.dependencies import get_session_store
from app.main import app
from app.services.event_publisher import NoopRuntimeEventPublisher
from app.services.prep import InterviewPlan, InterviewQuestion
from app.services.runtime_events import InterviewStreamDoneEvent
from app.services.session import InterviewSessionStore


_ORIGINAL_GET_REPORT_JOB_STORE = api_dependencies.get_report_job_store


class StreamingLLM:
    def generate_plan(self, job_description: str, resume_text: str) -> InterviewPlan:
        return InterviewPlan(
            title="Streaming report enqueue",
            questions=[
                InterviewQuestion(
                    id="q1",
                    kind="project",
                    prompt="Describe one project decision.",
                    focus="project depth",
                )
            ],
        )

    def generate_followup(self, context: list[dict[str, str]]) -> str:
        return "Please add the trade-off and validation result."

    def stream_followup(self, context: list[dict[str, str]]):
        yield "Please add the trade-off and validation result."


class RecordingReportJobStore:
    def __init__(self, session_store: InterviewSessionStore, order: list[str]):
        self.session_store = session_store
        self.order = order
        self.enqueue_attempts: list[str] = []
        self.jobs: dict[str, dict] = {}

    def enqueue_report_request(self, session_id: str) -> dict:
        self.enqueue_attempts.append(session_id)
        existing = self.jobs.get(session_id)
        if existing is not None:
            return dict(existing)
        self.order.append("job_authoritative")
        job = {
            "job_id": f"stream-job-{session_id}",
            "session_id": session_id,
            "status": "queued",
            "attempt_count": 0,
            "max_attempts": 3,
        }
        self.jobs[session_id] = job
        self.session_store.mark_report_processing(session_id)
        return dict(job)

    def get_job_by_session(self, session_id: str) -> dict | None:
        job = self.jobs.get(session_id)
        return dict(job) if job is not None else None


def _make_runtime(monkeypatch):
    order: list[str] = []
    store = InterviewSessionStore(llm=StreamingLLM())
    jobs = RecordingReportJobStore(store, order)
    app.dependency_overrides[get_session_store] = lambda: store
    app.dependency_overrides[api_dependencies.get_event_publisher] = (
        lambda: NoopRuntimeEventPublisher()
    )
    api_dependencies.get_report_job_store = lambda: jobs

    original_to_sse = InterviewStreamDoneEvent.to_sse

    def terminal_to_sse(self):
        order.append("terminal_event")
        return original_to_sse(self)

    monkeypatch.setattr(InterviewStreamDoneEvent, "to_sse", terminal_to_sse)
    return TestClient(app), store, jobs, order


def _start_final_stream(
    client: TestClient,
    store: InterviewSessionStore,
    *,
    command_id: str = "cmd-final",
):
    started = store.start(
        InterviewPlan(
            title="Streaming report enqueue",
            questions=[
                InterviewQuestion(
                    id="q1",
                    kind="project",
                    prompt="Describe one project decision.",
                    focus="project depth",
                )
            ],
        ),
        job_description="Backend role",
        resume_text="Built a backend service",
        job_tags=["backend"],
    )
    session_id = started.session_id
    first = client.post(
        f"/api/interviews/{session_id}/answer",
        json={"answer": "I chose cache-aside.", "command_id": "cmd-first"},
    )
    assert first.status_code == 200
    response = client.stream(
        "POST",
        f"/api/interviews/{session_id}/answer/stream",
        json={
            "answer": "The trade-off was stale reads; I validated p95 latency.",
            "command_id": command_id,
        },
    )
    return session_id, response


def teardown_function():
    app.dependency_overrides.clear()
    api_dependencies.get_report_job_store = _ORIGINAL_GET_REPORT_JOB_STORE


def test_stream_normal_eof_makes_job_authoritative_before_terminal_event(monkeypatch):
    client, store, jobs, order = _make_runtime(monkeypatch)
    session_id, stream = _start_final_stream(client, store)

    with stream as response:
        assert response.status_code == 200
        body = "".join(response.iter_text())

    assert "event: done" in body
    assert jobs.get_job_by_session(session_id)["job_id"]
    assert order.index("job_authoritative") < order.index("terminal_event")


def test_disconnect_after_terminal_and_refresh_keep_same_authoritative_job(
    monkeypatch,
):
    client, store, jobs, _ = _make_runtime(monkeypatch)
    session_id, stream = _start_final_stream(client, store)

    with stream as response:
        for line in response.iter_lines():
            if line == "event: done":
                break

    original = jobs.get_job_by_session(session_id)
    refreshed = client.get(
        f"/api/interviews/{session_id}/report/progress"
    )
    assert refreshed.status_code == 200
    assert refreshed.json()["report_job_id"] == original["job_id"]
    assert jobs.get_job_by_session(session_id)["job_id"] == original["job_id"]


def test_duplicate_stream_command_reuses_one_report_job(monkeypatch):
    client, store, jobs, _ = _make_runtime(monkeypatch)
    session_id, stream = _start_final_stream(
        client,
        store,
        command_id="cmd-final",
    )
    with stream as response:
        assert "event: done" in "".join(response.iter_text())

    duplicate = client.post(
        f"/api/interviews/{session_id}/answer/stream",
        json={
            "answer": "The trade-off was stale reads; I validated p95 latency.",
            "command_id": "cmd-final",
        },
    )

    assert duplicate.status_code == 200
    assert "event: done" in duplicate.text
    assert len(jobs.jobs) == 1
    assert len(set(job["job_id"] for job in jobs.jobs.values())) == 1
    assert jobs.enqueue_attempts == [session_id]
