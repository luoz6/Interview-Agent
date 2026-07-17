from copy import deepcopy

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
    prepare_interview as prepare_interview_service,
)
from app.services.question_evaluations import question_evaluation_from_feedback
from app.services.report import DimensionScores, InterviewFeedback, InterviewReport
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


def make_client(control_store=None):
    store = InterviewSessionStore(llm=FakeApiLLM())
    app.dependency_overrides[get_session_store] = lambda: store
    app.dependency_overrides[get_draft_store] = lambda: _api_draft_store
    if control_store is not None:
        app.dependency_overrides[
            route_module.get_runtime_control_store
        ] = lambda: control_store
    return TestClient(app)


class FakeRuntimeControl:
    def __init__(self):
        self.agent_filters = None
        self.event_filters = None

    def list_agent_runs(self, **kwargs):
        self.agent_filters = kwargs
        return [
            {
                "run_id": "agent-1",
                "correlation_id": kwargs["correlation_id"],
                "causation_id": "cmd-1",
                "agent": "examiner",
                "operation": "generate_followup",
                "phase": "interview",
                "session_id": kwargs["session_id"],
                "question_id": "q1",
                "state_version": 2,
                "command_id": "cmd-1",
                "evidence_ids": ["redis-1"],
                "attempt_number": 1,
                "status": "completed",
                "started_at": "2026-07-17T00:00:00Z",
                "finished_at": "2026-07-17T00:00:00.050000Z",
                "latency_ms": 50,
                "fallback_reason": None,
                "error_code": None,
                "output_type": "str",
            }
        ]

    def list_runtime_events(self, **kwargs):
        self.event_filters = kwargs
        return [
            {
                "event_id": "event-1",
                "correlation_id": "prep-1",
                "event_type": "round_closed",
                "schema_version": "runtime-event-v1",
                "status": "published",
                "attempt_count": 1,
                "max_attempts": 5,
                "replay_count": 0,
                "last_error_code": None,
                "created_at": "2026-07-17T00:00:00Z",
                "updated_at": "2026-07-17T00:00:00.050000Z",
                "published_at": "2026-07-17T00:00:00.050000Z",
                "dead_lettered_at": None,
            }
        ]


def teardown_function():
    app.dependency_overrides.clear()
    _api_draft_store.clear()


def test_health_endpoint():
    client = make_client()
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def start_runtime_api_session(client):
    response = client.post(
        "/api/interviews",
        json={
            "job_description": "Backend role using Redis.",
            "resume_text": "Built Redis-backed APIs.",
        },
    )
    assert response.status_code == 200
    return response.json()["session_id"]


def test_agent_runs_returns_only_safe_fields():
    control = FakeRuntimeControl()
    client = make_client(control)
    session_id = start_runtime_api_session(client)

    response = client.get(
        f"/api/interviews/{session_id}/agent-runs?agent=examiner"
    )
    item = response.json()["items"][0]

    assert set(item) == {
        "run_id",
        "correlation_id",
        "causation_id",
        "agent",
        "operation",
        "phase",
        "session_id",
        "question_id",
        "state_version",
        "command_id",
        "evidence_ids",
        "attempt_number",
        "status",
        "started_at",
        "finished_at",
        "latency_ms",
        "fallback_reason",
        "error_code",
        "output_type",
    }
    assert "safe_metadata" not in response.text
    assert control.agent_filters["agent"] == "examiner"


def test_runtime_events_excludes_payload_and_lease():
    control = FakeRuntimeControl()
    client = make_client(control)
    session_id = start_runtime_api_session(client)

    response = client.get(
        f"/api/interviews/{session_id}/runtime-events"
    )

    assert response.status_code == 200
    assert "payload_json" not in response.text
    assert "payload" not in response.text
    assert "lease_owner" not in response.text


def test_runtime_query_limit_above_one_hundred_is_rejected():
    control = FakeRuntimeControl()
    client = make_client(control)
    session_id = start_runtime_api_session(client)

    response = client.get(
        f"/api/interviews/{session_id}/agent-runs?limit=101"
    )

    assert response.status_code == 422


def test_prepare_endpoint_returns_questions(monkeypatch):
    monkeypatch.setattr(
        route_module,
        "prepare_interview",
        lambda job_description, resume_text, execution_runner=None: FakeApiLLM().generate_plan(
            job_description,
            resume_text,
        ),
    )
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


def test_prepare_endpoint_returns_job_tags_without_session_store(monkeypatch):
    def fail_session_store():
        raise RuntimeError("session store should not be used")

    app.dependency_overrides[get_session_store] = fail_session_store
    monkeypatch.setattr(
        route_module,
        "prepare_interview",
        lambda job_description, resume_text, execution_runner=None: prepare_interview_service(
            job_description,
            resume_text,
            llm=FakeApiLLM(),
        ),
    )
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


def test_question_evaluation_api_hides_internal_evidence_hashes():
    store = InterviewSessionStore(llm=FakeApiLLM())
    turn = store.start(
        InterviewPlan(
            title="Evaluation plan",
            questions=[
                InterviewQuestion(
                    id="q1",
                    kind="technical",
                    prompt="Explain Redis.",
                    focus="Redis",
                )
            ],
        ),
        job_description="Redis role",
        resume_text="Redis project",
        job_tags=["redis"],
    )
    feedback = InterviewFeedback(
        question_id="q1",
        question_text="Explain Redis.",
        user_answer="Cache aside.",
        score=80,
        dimension_scores=DimensionScores(
            breadth=80,
            depth=80,
            architecture=80,
            engineering=80,
            communication=80,
        ),
        rationale="Grounded evaluation.",
        critique="Add failure details.",
        better_answer="Explain fallback.",
        references=[],
    )
    record = question_evaluation_from_feedback(
        session_id=turn.session_id,
        feedback=feedback,
        retrieval_path="bound_evidence_ids",
        degraded_reason=None,
        evidence_content_sha256={"redis_consistency": "a" * 64},
    )
    store.upsert_question_evaluation(turn.session_id, record)
    app.dependency_overrides[get_session_store] = lambda: store
    client = TestClient(app)

    response = client.get(
        f"/api/interviews/{turn.session_id}/question-evaluations"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["items"][0]["retrieval_path"] == "bound_evidence_ids"
    assert "evidence_content_sha256" not in response.text
    assert "redis_consistency" not in response.text


def test_prepare_endpoint_does_not_require_session_store(monkeypatch):
    def fail_session_store():
        raise RuntimeError("session store should not be used")

    def fake_prepare_interview(
        job_description: str,
        resume_text: str,
        llm=None,
        execution_runner=None,
    ):
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
        json={
            "answer": "I used Redis to cache hot records.",
            "expected_version": 1,
            "command_id": "cmd-first",
        },
    )
    second = client.post(
        f"/api/interviews/{session_id}/answer",
        json={
            "answer": "I added delayed double delete.",
            "expected_version": 2,
            "command_id": "cmd-close",
        },
    )
    snapshot = client.get(f"/api/interviews/{session_id}").json()

    assert first.status_code == 200
    assert second.status_code == 200
    assert len(published) == 1
    assert published[0].question_id == "q1"
    assert published[0].answer_state == "answered"
    assert published[0].state_version == snapshot["state_version"]
    assert published[0].causation_id == "cmd-close"


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

    response = client.post(
        f"/api/interviews/{session_id}/skip",
        json={"expected_version": 1, "command_id": "cmd-skip"},
    )
    snapshot = client.get(f"/api/interviews/{session_id}").json()

    assert response.status_code == 200
    assert len(published) == 1
    assert published[0].question_id == "q1"
    assert published[0].answer_state == "skipped"
    assert published[0].state_version == snapshot["state_version"]
    assert published[0].causation_id == "cmd-skip"


def test_transactional_outbox_store_suppresses_direct_event_publish():
    published = []

    class FakePublisher:
        def publish(self, event):
            published.append(event)

    store = InterviewSessionStore(llm=FakeApiLLM())
    store.runtime_event_delivery = "transactional_outbox"
    turn = store.start(
        InterviewPlan(
            title="Backend interview",
            questions=[
                InterviewQuestion(
                    id="q1",
                    kind="technical",
                    prompt="Explain Redis.",
                    focus="Redis",
                )
            ],
        ),
        job_description="Backend role",
        resume_text="Built APIs",
        job_tags=["python"],
    )
    before = deepcopy(store.get(turn.session_id))
    store.skip(turn.session_id, command_id="cmd-skip")
    after = deepcopy(store.get(turn.session_id))

    route_module._publish_round_closed_event(
        FakePublisher(),
        store,
        before,
        after,
    )

    assert published == []


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
        json={
            "answer": "I used Redis to cache hot records.",
            "expected_version": 1,
            "command_id": "cmd-stream-first",
        },
    )
    with client.stream(
        "POST",
        f"/api/interviews/{started['session_id']}/answer/stream",
        json={
            "answer": "I added delayed double delete.",
            "expected_version": 2,
            "command_id": "cmd-stream-close",
        },
    ) as response:
        assert response.status_code == 200
        body = "".join(response.iter_text())

    assert "event: done" in body
    assert len(published) == 1
    assert published[0].question_id == "q1"
    assert published[0].answer_state == "answered"
    snapshot = client.get(
        f"/api/interviews/{started['session_id']}"
    ).json()
    assert published[0].state_version == snapshot["state_version"]
    assert published[0].causation_id == "cmd-stream-close"
