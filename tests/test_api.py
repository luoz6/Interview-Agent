from fastapi.testclient import TestClient

import app.api.routes as route_module
from app.api.routes import get_session_store
from app.main import app
from app.services.drafts import AnonymousDraftStore
from app.services.prep import (
    InterviewPlan,
    InterviewQuestion,
    KnowledgeBindingSnapshot,
    KnowledgeEvidenceRef,
    PrepContext,
    PrepQuestionHint,
)
from app.services.report import InterviewReport
from app.services.runtime import get_draft_store
from app.services.session import InterviewSessionStore


class FakeApiLLM:
    def __init__(self):
        self.last_context = None

    def generate_plan(self, job_description: str, resume_text: str):
        return InterviewPlan(
            title="LLM generated backend mock interview",
            questions=[
                InterviewQuestion(id="q1", kind="project", prompt="介绍一个项目。", focus="项目"),
                InterviewQuestion(id="q2", kind="technical", prompt="解释 Redis。", focus="Redis"),
                InterviewQuestion(
                    id="q3",
                    kind="system-design",
                    prompt="设计一个后端服务。",
                    focus="系统设计",
                ),
            ],
        )

    def generate_followup(self, context: list[dict[str, str]]) -> str:
        self.last_context = context
        return "请继续说明缓存失效时如何保护数据库。"

    def stream_followup(self, context: list[dict[str, str]]):
        self.last_context = context
        yield "请继续说明"
        yield "缓存失效时"
        yield "如何保护数据库。"

    def generate_report(
        self,
        plan: InterviewPlan,
        chunks: list[dict],
        session_id: str,
    ) -> InterviewReport:
        raise AssertionError("API flow tests do not generate reports")


_api_draft_store = AnonymousDraftStore()


def make_client():
    store = InterviewSessionStore(llm=FakeApiLLM())
    app.dependency_overrides[get_session_store] = lambda: store
    app.dependency_overrides[get_draft_store] = lambda: _api_draft_store
    return TestClient(app)


def teardown_function():
    app.dependency_overrides.clear()
    _api_draft_store.clear()


def test_health_endpoint():
    client = make_client()
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_prepare_endpoint_returns_questions():
    client = make_client()
    response = client.post(
        "/api/prep",
        json={
            "job_description": "Backend role using Python, FastAPI, Redis, and PostgreSQL.",
            "resume_text": "Built a FastAPI service with Redis cache.",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body["questions"]) >= 3


def test_prepare_endpoint_hides_internal_knowledge_hashes_and_binding_snapshot(monkeypatch):
    plan = InterviewPlan(
        title="Grounded plan",
        questions=[
            InterviewQuestion(
                id="q1",
                kind="technical",
                prompt="Explain Redis consistency.",
                focus="Redis",
            )
        ],
        prep_context=PrepContext(
            schema_version="v2",
            summary="Grounded prep.",
            knowledge_status="completed",
            evidence_refs=[
                KnowledgeEvidenceRef(
                    evidence_id="redis-consistency",
                    title="Redis consistency",
                    domain="redis",
                    source_type="theory",
                    score=0.93,
                    content_sha256="a" * 64,
                    corpus_manifest_sha256="b" * 64,
                    candidate_summary="用于说明缓存一致性考点。",
                )
            ],
            binding_snapshot=KnowledgeBindingSnapshot(
                prep_run_id="prep-secret-run",
                corpus_manifest_sha256="b" * 64,
                status="completed",
            ),
        ),
    )
    monkeypatch.setattr(route_module, "prepare_interview", lambda *_args, **_kwargs: plan)
    client = TestClient(app)

    response = client.post(
        "/api/prep",
        json={"job_description": "Redis role", "resume_text": "Redis project"},
    )

    assert response.status_code == 200
    body = response.json()
    evidence = body["prep_context"]["evidence_refs"][0]
    assert evidence == {
        "evidence_id": "redis-consistency",
        "title": "Redis consistency",
        "domain": "redis",
        "source_type": "theory",
        "candidate_summary": "用于说明缓存一致性考点。",
    }
    assert "binding_snapshot" not in body["prep_context"]
    assert "content_sha256" not in response.text
    assert "prep-secret-run" not in response.text


def test_prepare_endpoint_returns_job_tags_without_session_store():
    def fail_session_store():
        raise RuntimeError("session store should not be used")

    app.dependency_overrides[get_session_store] = fail_session_store
    client = TestClient(app)

    response = client.post(
        "/api/prep",
        json={
            "job_description": "Backend role using Python, FastAPI, Redis, and PostgreSQL.",
            "resume_text": "Built a FastAPI service with Redis cache.",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body["questions"]) >= 1
    assert body["job_tags"] == ["python", "fastapi", "redis", "postgresql"]
    assert body["prep_context"]["summary"].startswith("Knowledge Agent 预热了")
    assert body["prep_context"]["topics"]
    assert body["prep_context"]["question_hints"]
    assert body["prep_context"]["topics"][0]["id"].startswith("topic-")


def test_start_interview_persists_plan_prep_context_in_session_snapshot():
    client = make_client()
    started = client.post(
        "/api/interviews",
        json={
            "job_description": "Backend role using Python, FastAPI, Redis, and PostgreSQL.",
            "resume_text": "Built a FastAPI service with Redis cache.",
        },
    ).json()

    response = client.get(f"/api/interviews/{started['session_id']}")

    assert response.status_code == 200
    body = response.json()
    assert body["questions"][1]["id"] == "q2"
    assert body["messages"][0]["role"] == "interviewer"


def test_session_snapshot_restores_safe_public_evidence_binding(monkeypatch):
    plan = InterviewPlan(
        title="Grounded persisted plan",
        questions=[
            InterviewQuestion(
                id="q1",
                kind="technical",
                prompt="Explain Redis consistency.",
                focus="Redis",
            )
        ],
        prep_context=PrepContext(
            schema_version="v2",
            summary="Grounded prep.",
            knowledge_status="completed",
            evidence_refs=[
                KnowledgeEvidenceRef(
                    evidence_id="redis_consistency",
                    title="Redis Cache Consistency",
                    domain="redis",
                    source_type="theory",
                    score=0.91,
                    content_sha256="a" * 64,
                    corpus_manifest_sha256="b" * 64,
                    candidate_summary="缓存一致性机制提问依据。",
                )
            ],
            question_hints=[
                PrepQuestionHint(
                    question_id="q1",
                    evidence_ids=["redis_consistency"],
                    evidence_titles=["Redis Cache Consistency"],
                )
            ],
            binding_snapshot=KnowledgeBindingSnapshot(
                prep_run_id="private-prep-run",
                corpus_manifest_sha256="b" * 64,
                status="completed",
            ),
        ),
    )
    monkeypatch.setattr(route_module, "prepare_interview", lambda *_args, **_kwargs: plan)
    client = make_client()

    started = client.post(
        "/api/interviews",
        json={"job_description": "Redis role", "resume_text": "Redis project"},
    ).json()
    response = client.get(f"/api/interviews/{started['session_id']}")

    assert response.status_code == 200
    body = response.json()
    assert body["prep_context"]["knowledge_status"] == "completed"
    assert body["prep_context"]["question_hints"][0]["evidence_ids"] == [
        "redis_consistency"
    ]
    assert body["prep_context"]["evidence_refs"][0] == {
        "evidence_id": "redis_consistency",
        "title": "Redis Cache Consistency",
        "domain": "redis",
        "source_type": "theory",
        "candidate_summary": "缓存一致性机制提问依据。",
    }
    assert "content_sha256" not in response.text
    assert "private-prep-run" not in response.text


def test_prepare_endpoint_does_not_require_session_store(monkeypatch):
    def fail_session_store():
        raise RuntimeError("session store should not be used")

    def fake_prepare_interview(job_description: str, resume_text: str, llm=None):
        assert llm is None
        return InterviewPlan(
            title="Preview plan",
            questions=[
                InterviewQuestion(
                    id="q1",
                    kind="technical",
                    prompt="Explain caching.",
                    focus="cache",
                )
            ],
        )

    app.dependency_overrides[get_session_store] = fail_session_store
    monkeypatch.setattr(route_module, "prepare_interview", fake_prepare_interview)
    client = TestClient(app)

    response = client.post(
        "/api/prep",
        json={
            "job_description": "Backend role using Redis.",
            "resume_text": "Built Redis-backed APIs.",
        },
    )

    assert response.status_code == 200
    assert response.json()["title"] == "Preview plan"


def test_create_interview_draft_returns_anonymous_draft():
    client = make_client()

    response = client.post(
        "/api/interview-drafts",
        json={
            "job_description": "Backend role using Python and Redis.",
            "resume_text": "Built Redis-backed APIs.",
            "title": "Backend draft",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["draft_id"].startswith("draft_")
    assert body["job_description"] == "Backend role using Python and Redis."
    assert body["resume_text"] == "Built Redis-backed APIs."
    assert body["job_tags"] == ["python", "redis"]
    assert body["title"] == "Backend draft"
    assert body["created_at"]
    assert body["updated_at"]


def test_update_interview_draft_reuses_draft_id():
    client = make_client()
    created = client.post(
        "/api/interview-drafts",
        json={
            "job_description": "Backend role using Python.",
            "resume_text": "Built APIs.",
        },
    ).json()

    response = client.post(
        "/api/interview-drafts",
        json={
            "draft_id": created["draft_id"],
            "job_description": "Backend role using Python and FastAPI.",
            "resume_text": "Built FastAPI APIs.",
            "job_tags": ["python", "fastapi"],
            "title": "Updated draft",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["draft_id"] == created["draft_id"]
    assert body["created_at"] == created["created_at"]
    assert body["job_tags"] == ["python", "fastapi"]
    assert body["title"] == "Updated draft"


def test_get_interview_draft_returns_saved_payload():
    client = make_client()
    created = client.post(
        "/api/interview-drafts",
        json={
            "job_description": "Backend role using Redis.",
            "resume_text": "Built cache APIs.",
        },
    ).json()

    response = client.get(f"/api/interview-drafts/{created['draft_id']}")

    assert response.status_code == 200
    body = response.json()
    assert body["draft_id"] == created["draft_id"]
    assert body["job_description"] == "Backend role using Redis."
    assert body["job_tags"] == ["redis"]


def test_get_interview_draft_missing_returns_404():
    client = make_client()

    response = client.get("/api/interview-drafts/missing")

    assert response.status_code == 404
    assert response.json()["detail"] == "draft not found"


def test_create_interview_draft_rejects_blank_fields():
    client = make_client()

    response = client.post(
        "/api/interview-drafts",
        json={
            "job_description": "   ",
            "resume_text": "Built APIs.",
        },
    )

    assert response.status_code == 422


def test_interview_answer_flow():
    client = make_client()
    start_response = client.post(
        "/api/interviews",
        json={
            "job_description": "Backend role using Python and Redis.",
            "resume_text": "Built a Python API with Redis.",
        },
    )

    assert start_response.status_code == 200
    started = start_response.json()
    assert started["session_id"]
    assert started["current_question"]

    answer_response = client.post(
        f"/api/interviews/{started['session_id']}/answer",
        json={"answer": "I used Redis to cache frequently requested records."},
    )

    assert answer_response.status_code == 200
    answered = answer_response.json()
    assert answered["session_id"] == started["session_id"]
    assert answered["follow_up"] == "请继续说明缓存失效时如何保护数据库。"


def test_get_interview_session_returns_snapshot():
    client = make_client()
    start_response = client.post(
        "/api/interviews",
        json={
            "job_description": "Backend role using Python, FastAPI, Redis, and PostgreSQL.",
            "resume_text": "Built a Python API with Redis.",
        },
    )
    started = start_response.json()

    response = client.get(f"/api/interviews/{started['session_id']}")

    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == started["session_id"]
    assert body["status"] == "active"
    assert body["current_index"] == 0
    assert body["total_questions"] == 3
    assert body["completed_questions"] == 0
    assert body["job_tags"] == ["python", "fastapi", "redis", "postgresql"]
    assert body["current_question"]["id"] == "q1"
    assert body["questions"][0]["id"] == "q1"
    assert [question["state"] for question in body["questions"]] == [
        "current",
        "pending",
        "pending",
    ]
    assert body["messages"][0]["role"] == "interviewer"


def test_get_interview_session_returns_resume_metadata():
    client = make_client()
    started = client.post(
        "/api/interviews",
        json={
            "job_description": "Backend role using Python and Redis.",
            "resume_text": "Built a Python API with Redis.",
        },
    ).json()

    response = client.get(f"/api/interviews/{started['session_id']}")

    assert response.status_code == 200
    body = response.json()
    assert body["phase"] == "interview"
    assert body["phase_status"] == "active"
    assert body["review_status"] == "idle"
    assert body["state_version"] == 1
    assert body["checkpoint_version"] == 1
    assert body["last_checkpoint_at"]
    assert body["last_command_id"] is None


def test_get_interview_session_returns_skip_and_timing_metadata():
    client = make_client()
    start_response = client.post(
        "/api/interviews",
        json={
            "job_description": "Backend role using Python and Redis.",
            "resume_text": "Built a Python API with Redis.",
        },
    )
    session_id = start_response.json()["session_id"]

    client.post(f"/api/interviews/{session_id}/skip")
    response = client.get(f"/api/interviews/{session_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["started_at"]
    assert body["finished_at"] is None
    assert isinstance(body["elapsed_seconds"], int)
    assert body["answered_questions"] == 0
    assert body["skipped_questions"] == 1
    assert body["unanswered_questions"] == 2
    assert body["questions"][0]["state"] == "skipped"
    assert body["questions"][1]["state"] == "current"


def test_get_interview_session_missing_returns_404():
    client = make_client()

    response = client.get("/api/interviews/missing")

    assert response.status_code == 404
    assert response.json()["detail"] == "session not found"


def test_skip_endpoint_advances_question():
    client = make_client()
    start_response = client.post(
        "/api/interviews",
        json={
            "job_description": "Backend role using Python and Redis.",
            "resume_text": "Built a Python API with Redis.",
        },
    )
    session_id = start_response.json()["session_id"]

    response = client.post(f"/api/interviews/{session_id}/skip")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "active"
    assert body["current_question"]["id"] == "q2"
    assert body["follow_up"] is None


def test_answer_missing_session_returns_404():
    client = make_client()

    response = client.post(
        "/api/interviews/missing/answer",
        json={"answer": "I used Redis."},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "session not found"


def test_answer_route_returns_409_for_version_conflict():
    client = make_client()
    started = client.post(
        "/api/interviews",
        json={
            "job_description": "Backend role using Python and Redis.",
            "resume_text": "Built a Python API with Redis.",
        },
    ).json()

    response = client.post(
        f"/api/interviews/{started['session_id']}/answer",
        json={
            "answer": "I used Redis.",
            "expected_version": 0,
            "command_id": "cmd-1",
        },
    )

    assert response.status_code == 409
    assert response.json() == {
        "detail": "session version conflict",
        "expected_version": 0,
        "actual_version": 1,
    }


def test_interview_answer_stream_flow():
    client = make_client()
    start_response = client.post(
        "/api/interviews",
        json={
            "job_description": "Backend role using Python and Redis.",
            "resume_text": "Built a Python API with Redis.",
        },
    )
    started = start_response.json()

    with client.stream(
        "POST",
        f"/api/interviews/{started['session_id']}/answer/stream",
        json={
            "answer": "I used Redis to cache frequently requested records.",
            "expected_version": 1,
            "command_id": "cmd-stream",
        },
    ) as response:
        assert response.status_code == 200
        body = "".join(response.iter_text())
    snapshot = client.get(f"/api/interviews/{started['session_id']}").json()

    assert "event: chunk" in body
    assert "event: done" in body
    assert snapshot["state_version"] == 3
    assert snapshot["checkpoint_version"] == 3
    assert snapshot["last_command_id"] == "cmd-stream"
    assert "请继续说明" in body
    assert "缓存失效时" in body


def test_interview_moves_to_next_question_after_followup_answer():
    client = make_client()
    start_response = client.post(
        "/api/interviews",
        json={
            "job_description": "Backend role using Python and Redis.",
            "resume_text": "Built a Python API with Redis.",
        },
    )
    started = start_response.json()

    first_answer = client.post(
        f"/api/interviews/{started['session_id']}/answer",
        json={"answer": "I used Redis to cache hot records."},
    ).json()
    second_answer = client.post(
        f"/api/interviews/{started['session_id']}/answer",
        json={"answer": "I use logical expiration and rate limiting."},
    ).json()

    assert first_answer["follow_up"] == "请继续说明缓存失效时如何保护数据库。"
    assert second_answer["follow_up"] is None
    assert second_answer["current_question"]["id"] == "q2"
    assert second_answer["status"] == "active"


def test_answer_route_publishes_round_closed_event_only_when_question_closes():
    published = []

    class FakePublisher:
        def publish(self, event):
            published.append(event)

    app.dependency_overrides[route_module.get_event_publisher] = lambda: FakePublisher()
    client = make_client()

    start_response = client.post(
        "/api/interviews",
        json={
            "job_description": "Backend role using Python and Redis.",
            "resume_text": "Built a Python API with Redis.",
        },
    )
    session_id = start_response.json()["session_id"]

    first = client.post(
        f"/api/interviews/{session_id}/answer",
        json={"answer": "I used Redis to cache hot records."},
    )
    second = client.post(
        f"/api/interviews/{session_id}/answer",
        json={"answer": "I added delayed double delete."},
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert len(published) == 1
    assert published[0].question_id == "q1"
    assert published[0].answer_state == "answered"


def test_skip_route_publishes_round_closed_event():
    published = []

    class FakePublisher:
        def publish(self, event):
            published.append(event)

    app.dependency_overrides[route_module.get_event_publisher] = lambda: FakePublisher()
    client = make_client()

    start_response = client.post(
        "/api/interviews",
        json={
            "job_description": "Backend role using Python and Redis.",
            "resume_text": "Built a Python API with Redis.",
        },
    )
    session_id = start_response.json()["session_id"]

    response = client.post(f"/api/interviews/{session_id}/skip")

    assert response.status_code == 200
    assert len(published) == 1
    assert published[0].question_id == "q1"
    assert published[0].answer_state == "skipped"


def test_answer_route_succeeds_when_round_closed_publish_fails():
    attempts = []

    class FailingPublisher:
        def publish(self, event):
            attempts.append(event)
            raise RuntimeError("broker down")

    app.dependency_overrides[route_module.get_event_publisher] = (
        lambda: FailingPublisher()
    )
    client = make_client()

    start_response = client.post(
        "/api/interviews",
        json={
            "job_description": "Backend role using Python and Redis.",
            "resume_text": "Built a Python API with Redis.",
        },
    )
    session_id = start_response.json()["session_id"]

    first = client.post(
        f"/api/interviews/{session_id}/answer",
        json={"answer": "I used Redis to cache hot records."},
    )
    second = client.post(
        f"/api/interviews/{session_id}/answer",
        json={"answer": "I added delayed double delete."},
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["current_question"]["id"] == "q2"
    assert len(attempts) == 1


def test_answer_stream_returns_done_when_round_closed_publish_fails():
    attempts = []

    class FailingPublisher:
        def publish(self, event):
            attempts.append(event)
            raise RuntimeError("broker down")

    app.dependency_overrides[route_module.get_event_publisher] = (
        lambda: FailingPublisher()
    )
    client = make_client()

    start_response = client.post(
        "/api/interviews",
        json={
            "job_description": "Backend role using Python and Redis.",
            "resume_text": "Built a Python API with Redis.",
        },
    )
    session_id = start_response.json()["session_id"]

    client.post(
        f"/api/interviews/{session_id}/answer",
        json={"answer": "I used Redis to cache hot records."},
    )
    with client.stream(
        "POST",
        f"/api/interviews/{session_id}/answer/stream",
        json={"answer": "I added delayed double delete."},
    ) as response:
        assert response.status_code == 200
        body = "".join(response.iter_text())

    assert "event: done" in body
    assert "event: error" not in body
    assert len(attempts) == 1


def test_answer_stream_publishes_round_closed_event_when_streamed_answer_closes_question():
    published = []

    class FakePublisher:
        def publish(self, event):
            published.append(event)

    app.dependency_overrides[route_module.get_event_publisher] = lambda: FakePublisher()
    client = make_client()

    started = client.post(
        "/api/interviews",
        json={
            "job_description": "Backend role using Python and Redis.",
            "resume_text": "Built a Python API with Redis.",
        },
    ).json()

    client.post(
        f"/api/interviews/{started['session_id']}/answer",
        json={"answer": "I used Redis to cache hot records."},
    )
    with client.stream(
        "POST",
        f"/api/interviews/{started['session_id']}/answer/stream",
        json={"answer": "I added delayed double delete."},
    ) as response:
        assert response.status_code == 200
        body = "".join(response.iter_text())

    assert "event: done" in body
    assert len(published) == 1
    assert published[0].question_id == "q1"
    assert published[0].answer_state == "answered"
