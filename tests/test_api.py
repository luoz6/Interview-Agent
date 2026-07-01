from fastapi.testclient import TestClient

from app.api.routes import get_session_store
from app.main import app
from app.services.prep import InterviewPlan, InterviewQuestion
from app.services.session import InterviewSessionStore


class FakeApiLLM:
    def __init__(self):
        self.last_context = None

    def generate_plan(self, job_description: str, resume_text: str):
        return InterviewPlan(
            title="LLM 生成的后端模拟面试",
            questions=[
                InterviewQuestion(id="q1", kind="project", prompt="请介绍一个项目。", focus="项目"),
                InterviewQuestion(id="q2", kind="technical", prompt="请解释 Redis。", focus="Redis"),
                InterviewQuestion(id="q3", kind="system-design", prompt="请设计服务。", focus="系统设计"),
            ],
        )

    def generate_followup(self, context: list[dict[str, str]]) -> str:
        self.last_context = context
        return "请继续说明缓存失效时如何保护数据库。"


def make_client():
    store = InterviewSessionStore(llm=FakeApiLLM())
    app.dependency_overrides[get_session_store] = lambda: store
    return TestClient(app)


def teardown_function():
    app.dependency_overrides.clear()


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
    assert answered["follow_up"] or answered["current_question"]


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
