from fastapi.testclient import TestClient

import app.api.routes as route_module
from app.api.routes import get_session_store
from app.main import app
from app.services.prep import InterviewPlan, InterviewQuestion
from app.services.report import (
    DimensionScores,
    InterviewFeedback,
    InterviewReport,
)
from app.services.session import InterviewSessionStore
from app.services.vector_store import KnowledgeChunk


_ORIGINAL_GET_REPORT_JOB_STORE = route_module.get_report_job_store


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
        evaluation_items: list[dict],
        session_id: str,
    ) -> InterviewReport:
        self.report_calls += 1
        return InterviewReport(
            session_id=session_id,
            overall_score=81,
            overall_dimension_scores=make_dimension_scores(81),
            summary="Clear project story with practical tradeoffs.",
            highlights=["Explained tradeoffs"],
            feedbacks=[
                InterviewFeedback(
                    question_id="q1",
                    question_text="Introduce a backend project.",
                    user_answer="The candidate built a backend cache service.",
                    score=81,
                    dimension_scores=make_dimension_scores(81),
                    rationale="The answer covered implementation tradeoffs clearly.",
                    critique="Needs stronger business metrics.",
                    better_answer="I reduced p95 latency using cache-aside Redis.",
                    references=[],
                )
            ],
        )


def make_dimension_scores(score: int = 81) -> DimensionScores:
    return DimensionScores(
        breadth=score,
        depth=score,
        architecture=score,
        engineering=score,
        communication=score,
    )


def make_client():
    class FakeReportJobStore:
        def __init__(self, store: InterviewSessionStore) -> None:
            self._store = store
            self.enqueue_calls: list[str] = []
            self._jobs_by_session: dict[str, dict] = {}

        def enqueue_report_request(self, session_id: str) -> dict:
            self.enqueue_calls.append(session_id)
            job = {
                "job_id": f"job-{len(self.enqueue_calls)}",
                "session_id": session_id,
                "status": "queued",
            }
            self._jobs_by_session[session_id] = job
            self._store.mark_report_processing(session_id)
            return job

        def get_job_by_session(self, session_id: str) -> dict | None:
            return self._jobs_by_session.get(session_id)

    class FakeVectorStore:
        def search(self, query_text: str, *, job_tags: list[str], source_types=None, limit=5):
            return [
                KnowledgeChunk(
                    chunk_id="redis-1",
                    title="Redis cache consistency",
                    content="Delete cache after database writes.",
                    source_type="theory",
                    domain="redis",
                    tags=["redis"],
                    metadata={"section": "consistency"},
                    score=0.9,
                )
            ]

    import app.services.report_tasks as report_tasks

    report_tasks.get_knowledge_store = lambda: FakeVectorStore()
    llm = ReportApiLLM()
    store = InterviewSessionStore(llm=llm)
    job_store = FakeReportJobStore(store)
    app.dependency_overrides[get_session_store] = lambda: store
    route_module.get_report_job_store = lambda: job_store
    return TestClient(app), store, llm, job_store


def teardown_function():
    app.dependency_overrides.clear()
    route_module.get_report_job_store = _ORIGINAL_GET_REPORT_JOB_STORE


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
    client, _, _, _ = make_client()

    response = client.get("/api/interviews/missing/report")

    assert response.status_code == 404


def test_report_endpoint_rejects_active_interview():
    client, _, _, _ = make_client()
    session_id = start_interview(client)

    response = client.get(f"/api/interviews/{session_id}/report")

    assert response.status_code == 404


def test_report_endpoint_returns_202_with_progress():
    client, store, _, _ = make_client()
    session_id = start_interview(client)
    finish_session(store, session_id)
    store.mark_report_processing(session_id)

    response = client.get(f"/api/interviews/{session_id}/report")

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "processing"
    assert body["progress"]["stage"] == "retrieving"
    assert body["progress"]["percent"] == 20


def test_report_progress_endpoint_returns_queued_detail_before_report_record_exists():
    client, store, _, _ = make_client()
    session_id = start_interview(client)
    finish_session(store, session_id)

    response = client.get(f"/api/interviews/{session_id}/report/progress")

    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == session_id
    assert body["report_job_id"] is None
    assert body["status"] == "processing"
    assert body["stage"] == "queued"
    assert body["percent"] == 0
    assert body["events"] == []
    assert body["rag"] == {
        "top_k": 5,
        "source_types": ["theory", "expert_benchmark"],
        "matched_chunks": None,
    }


def test_report_progress_endpoint_returns_processing_detail():
    client, store, _, _ = make_client()
    session_id = start_interview(client)
    finish_session(store, session_id)
    store.mark_report_processing(session_id)

    response = client.get(f"/api/interviews/{session_id}/report/progress")

    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == session_id
    assert body["status"] == "processing"
    assert body["stage"] == "retrieving"
    assert body["percent"] == 20
    assert body["message"] == "Retrieving role-specific knowledge references."
    assert body["events"] == [
        {
            "stage": "retrieving",
            "message": "Retrieving role-specific knowledge references.",
        }
    ]
    assert body["rag"]["top_k"] == 5
    assert body["rag"]["source_types"] == ["theory", "expert_benchmark"]


def test_report_progress_endpoint_returns_report_job_id_after_finish_enqueue():
    client, _, _, _ = make_client()
    session_id = start_interview(client)

    finish_response = client.post(f"/api/interviews/{session_id}/finish")
    progress_response = client.get(f"/api/interviews/{session_id}/report/progress")

    assert finish_response.status_code == 200
    assert progress_response.status_code == 200
    assert progress_response.json()["report_job_id"] == "job-1"


def test_report_progress_endpoint_returns_completed_detail():
    client, store, _, _ = make_client()
    session_id = start_interview(client)
    finish_session(store, session_id)
    store.save_report(
        session_id,
        InterviewReport(
            session_id=session_id,
            overall_score=81,
            overall_dimension_scores=make_dimension_scores(81),
            summary="Clear project story with practical tradeoffs.",
            highlights=["Explained tradeoffs"],
            feedbacks=[
                InterviewFeedback(
                    question_id="q1",
                    question_text="Introduce a backend project.",
                    user_answer="The candidate built a backend cache service.",
                    score=81,
                    dimension_scores=make_dimension_scores(81),
                    rationale="The answer covered implementation tradeoffs clearly.",
                    critique="Needs stronger business metrics.",
                    better_answer="I reduced p95 latency using cache-aside Redis.",
                    references=[],
                )
            ],
        ),
    )

    response = client.get(f"/api/interviews/{session_id}/report/progress")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["stage"] == "completed"
    assert body["percent"] == 100
    assert body["events"] == [{"stage": "completed", "message": "Report completed."}]


def test_report_progress_endpoint_rejects_active_interview():
    client, _, _, _ = make_client()
    session_id = start_interview(client)

    response = client.get(f"/api/interviews/{session_id}/report/progress")

    assert response.status_code == 404
    assert response.json()["detail"] == "interview is not finished"


def test_finished_answer_enqueues_report_generation_once_and_leaves_processing():
    client, store, llm, job_store = make_client()
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
    assert job_store.enqueue_calls == [session_id]
    assert llm.report_calls == 0
    record = store.get_report_record(session_id)
    assert record is not None
    assert record.status == "processing"

    report_response = client.get(f"/api/interviews/{session_id}/report")
    assert report_response.status_code == 202
    assert report_response.json()["status"] == "processing"


def test_finish_endpoint_enqueues_report_generation_once_and_is_idempotent():
    client, store, llm, job_store = make_client()
    session_id = start_interview(client)

    first_response = client.post(f"/api/interviews/{session_id}/finish")
    second_response = client.post(f"/api/interviews/{session_id}/finish")

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    assert first_response.json()["status"] == "finished"
    assert first_response.json()["current_question"] is None
    assert first_response.json()["follow_up"] == "本次模拟面试已结束。"
    assert second_response.json()["status"] == "finished"
    assert job_store.enqueue_calls == [session_id]
    assert llm.report_calls == 0
    assert len(
        [
            message
            for message in store.get(session_id)["messages"]
            if message["content"] == "本次模拟面试已结束。"
        ]
    ) == 1

    report_response = client.get(f"/api/interviews/{session_id}/report")
    assert report_response.status_code == 202
    assert report_response.json()["status"] == "processing"


def test_finished_answer_falls_back_to_in_memory_report_generation_when_job_store_is_unavailable():
    client, store, llm, _ = make_client()
    route_module.get_report_job_store = lambda: (_ for _ in ()).throw(
        RuntimeError("POSTGRES_DSN is required to build report job store")
    )
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
    assert record is not None
    assert record.status == "completed"

    report_response = client.get(f"/api/interviews/{session_id}/report")
    assert report_response.status_code == 200
    assert report_response.json()["overall_score"] == 81


def test_report_endpoint_returns_500_for_failed_report():
    client, store, _, _ = make_client()
    session_id = start_interview(client)
    finish_session(store, session_id)
    store.mark_report_processing(session_id)
    store.fail_report(session_id, "report generation timed out")

    response = client.get(f"/api/interviews/{session_id}/report")

    assert response.status_code == 500
    assert response.json()["detail"] == "report generation timed out"


def test_report_endpoint_returns_retrieval_unavailable_failure_detail():
    client, store, _, _ = make_client()
    session_id = start_interview(client)
    finish_session(store, session_id)
    store.mark_report_processing(session_id)
    store.fail_report(session_id, "pgvector knowledge store is unavailable")

    response = client.get(f"/api/interviews/{session_id}/report")

    assert response.status_code == 500
    assert response.json()["detail"] == "pgvector knowledge store is unavailable"


def test_report_endpoint_returns_fallback_report_for_evidence_insufficient():
    client, store, _, _ = make_client()
    session_id = start_interview(client)
    finish_session(store, session_id)
    store.save_report(
        session_id,
        InterviewReport(
            session_id=session_id,
            overall_score=60,
            overall_dimension_scores=make_dimension_scores(60),
            summary="Evidence was insufficient for a grounded expert report.",
            highlights=["Completed the mock interview"],
            is_fallback=True,
            feedbacks=[
                InterviewFeedback(
                    question_id="q1",
                    question_text="Introduce a backend project.",
                    user_answer="The candidate built a backend cache service.",
                    score=60,
                    dimension_scores=make_dimension_scores(60),
                    rationale="Fallback report generated because grounded evidence was insufficient.",
                    critique="Needs stronger business metrics.",
                    better_answer="I reduced p95 latency using cache-aside Redis.",
                    references=[],
                )
            ],
        ),
    )

    response = client.get(f"/api/interviews/{session_id}/report")

    assert response.status_code == 200
    body = response.json()
    assert body["is_fallback"] is True
    assert body["summary"] == "Evidence was insufficient for a grounded expert report."
    assert body["feedbacks"][0]["references"] == []
