from fastapi.testclient import TestClient

from app.api.routes import get_session_store
from app.main import app
from app.services.prep import InterviewPlan, InterviewQuestion
from app.services.report import InterviewFeedback, InterviewReport
from app.services.session import InterviewSessionStore


class ReportApiLLM:
    def __init__(self) -> None:
        self.report_calls = 0

    def generate_plan(self, job_description: str, resume_text: str) -> InterviewPlan:
        return InterviewPlan(
            title="Backend mock interview",
            questions=[
                InterviewQuestion(
                    id="q1",
                    kind="project",
                    prompt="Introduce a backend project.",
                    focus="project depth",
                )
            ],
        )

    def generate_followup(self, context: list[dict[str, str]]) -> str:
        return "Please explain the tradeoffs."

    def generate_report(
        self,
        plan: InterviewPlan,
        chunks: list[dict],
        session_id: str,
    ) -> InterviewReport:
        self.report_calls += 1
        return InterviewReport(
            session_id=session_id,
            overall_score=81,
            summary="Clear project story with practical tradeoffs.",
            highlights=["Explained tradeoffs"],
            feedbacks=[
                InterviewFeedback(
                    question_id="q1",
                    question_text="Introduce a backend project.",
                    user_answer="The candidate built a backend cache service.",
                    score=81,
                    critique="Needs stronger business metrics.",
                    better_answer="I reduced p95 latency using cache-aside Redis.",
                )
            ],
        )


def make_client():
    llm = ReportApiLLM()
    store = InterviewSessionStore(llm=llm)
    app.dependency_overrides[get_session_store] = lambda: store
    return TestClient(app), store, llm


def teardown_function():
    app.dependency_overrides.clear()


def start_interview(client: TestClient) -> str:
    response = client.post(
        "/api/interviews",
        json={
            "job_description": "Backend role using Python and Redis.",
            "resume_text": "Built a Python API with Redis.",
        },
    )
    assert response.status_code == 200
    return response.json()["session_id"]


def finish_session(store: InterviewSessionStore, session_id: str) -> None:
    state = store.get(session_id)
    state["status"] = "finished"
    state["current_index"] = len(state["plan"].questions)


def test_report_endpoint_returns_404_for_unknown_session():
    client, _, _ = make_client()

    response = client.get("/api/interviews/missing/report")

    assert response.status_code == 404


def test_report_endpoint_rejects_active_interview():
    client, _, _ = make_client()
    session_id = start_interview(client)

    response = client.get(f"/api/interviews/{session_id}/report")

    assert response.status_code == 404


def test_report_endpoint_returns_202_while_processing():
    client, store, _ = make_client()
    session_id = start_interview(client)
    finish_session(store, session_id)
    store.mark_report_processing(session_id)

    response = client.get(f"/api/interviews/{session_id}/report")

    assert response.status_code == 202


def test_finished_answer_triggers_report_generation_once():
    client, store, llm = make_client()
    session_id = start_interview(client)

    first_response = client.post(
        f"/api/interviews/{session_id}/answer",
        json={"answer": "I built a Redis-backed service."},
    )
    second_response = client.post(
        f"/api/interviews/{session_id}/answer",
        json={"answer": "I used cache-aside and database fallback."},
    )

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    assert second_response.json()["status"] == "finished"
    assert llm.report_calls == 1
    record = store.get_report_record(session_id)
    assert record.status == "completed"
    assert record.report.overall_score == 81

    report_response = client.get(f"/api/interviews/{session_id}/report")
    assert report_response.status_code == 200
    assert report_response.json()["overall_score"] == 81


def test_report_endpoint_returns_500_for_failed_report():
    client, store, _ = make_client()
    session_id = start_interview(client)
    finish_session(store, session_id)
    store.mark_report_processing(session_id)
    store.fail_report(session_id, "report generation timed out")

    response = client.get(f"/api/interviews/{session_id}/report")

    assert response.status_code == 500
    assert response.json()["detail"] == "report generation timed out"
