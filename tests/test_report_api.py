import pytest
from fastapi.testclient import TestClient

import app.api.routes as route_module
from app.api.routes import get_session_store
from app.main import app
from app.services.prep import InterviewPlan, InterviewQuestion
from app.services.in_memory_interview_launch_repository import InMemoryInterviewLaunchRepository
from app.services.in_memory_prep_plan_store import InMemoryPrepPlanStore
from app.services.question_evaluations import question_evaluation_from_feedback
from app.services.report import (
    DimensionScores,
    InterviewFeedback,
    InterviewReport,
    ReportProgress,
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
                ),
                InterviewQuestion(
                    id="q2",
                    kind="technical",
                    prompt="Explain a cache consistency decision.",
                    focus="technical depth",
                ),
                InterviewQuestion(
                    id="q3",
                    kind="system-design",
                    prompt="Design the service for ten times the traffic.",
                    focus="system design",
                ),
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
            summary=(
                "\u56de\u7b54\u4e3b\u7ebf\u6e05\u6670\uff0c\u80fd\u8bf4\u6e05 Redis "
                "\u7f13\u5b58\u4e00\u81f4\u6027\u3001\u6570\u636e\u5e93\u4fdd\u62a4"
                "\u548c\u964d\u7ea7\u7b56\u7565\u3002"
            ),
            highlights=[
                "\u8bf4\u6e05\u4e86 Redis \u53d6\u820d\u3001\u56de\u9000\u548c\u76d1\u63a7\u601d\u8def"
            ],
            feedbacks=[
                InterviewFeedback(
                    question_id="q1",
                    question_text="Introduce a backend project.",
                    user_answer="The candidate built a backend cache service.",
                    score=81,
                    dimension_scores=make_dimension_scores(81),
                    applicable_dimensions=[
                        "breadth",
                        "depth",
                        "architecture",
                        "engineering",
                        "communication",
                    ],
                    dimension_evidence=[
                        {
                            "dimension": "engineering",
                            "observed": [
                                "\u5019\u9009\u4eba\u8bf4\u660e\u4e86 cache-aside \u6d41\u7a0b\u548c\u7f13\u5b58\u5931\u6548\u4fdd\u5e95\u5904\u7406\u3002"
                            ],
                            "missing": [
                                "\u8fd8\u9700\u8865\u5145\u5ef6\u8fdf\u53cc\u5220\u548c\u91cf\u5316\u6536\u76ca\u3002"
                            ],
                            "quality_signals": ["concept", "concrete_steps"],
                        }
                    ],
                    rationale=(
                        "\u7b54\u6848\u8bf4\u6e05\u4e86 cache-aside \u6d41\u7a0b\uff0c"
                        "\u4e5f\u63d0\u5230\u4e86\u7f13\u5b58\u5931\u6548\u540e\u7684\u4fdd\u5e95\u5904\u7406\u3002"
                    ),
                    critique=(
                        "\u4f46\u8fd8\u53ef\u4ee5\u8865\u5145\u5ef6\u8fdf\u53cc\u5220\u3001"
                        "\u544a\u8b66\u6307\u6807\u548c\u91cf\u5316\u6536\u76ca\u3002"
                    ),
                    better_answer=(
                        "\u5efa\u8bae\u8865\u5145\u5ef6\u8fdf\u53cc\u5220\u3001Redis \u5f02\u5e38\u964d\u7ea7"
                        "\u3001p95 \u4f18\u5316\u6570\u636e\u548c\u76d1\u63a7\u95ed\u73af\u3002"
                    ),
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


def make_report_model(
    session_id: str,
    *,
    score: int = 81,
    summary: str = "Clear project story with practical tradeoffs.",
) -> InterviewReport:
    return InterviewReport(
        session_id=session_id,
        overall_score=score,
        overall_dimension_scores=make_dimension_scores(score),
        summary=summary,
        highlights=["Explained tradeoffs"],
        feedbacks=[
            InterviewFeedback(
                question_id="q1",
                question_text="Introduce a backend project.",
                user_answer="The candidate built a backend cache service.",
                score=score,
                dimension_scores=make_dimension_scores(score),
                rationale="The answer covered implementation tradeoffs clearly.",
                critique="Needs stronger business metrics.",
                better_answer="I reduced p95 latency using cache-aside Redis.",
                references=[],
            )
        ],
    )


def make_client():
    class FakeReportJobStore:
        def __init__(self, store: InterviewSessionStore) -> None:
            self._store = store
            self.enqueue_calls: list[str] = []
            self._jobs_by_session: dict[str, dict] = {}
            self.requeue_calls: list[str] = []
            self.raise_requeue_race = False

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

        def seed_job(self, session_id: str, *, status: str) -> dict:
            job = {
                "job_id": f"job-{session_id}",
                "session_id": session_id,
                "status": status,
                "replay_count": 0,
            }
            self._jobs_by_session[session_id] = job
            return job

        def requeue_failed(self, session_id: str) -> dict:
            self.requeue_calls.append(session_id)
            if self.raise_requeue_race:
                raise ValueError("simulated status race")
            job = self._jobs_by_session.get(session_id)
            if job is None or job["status"] != "failed":
                raise ValueError("report job is not failed")
            self._store.mark_report_processing(session_id)
            job["status"] = "queued"
            job["replay_count"] += 1
            return dict(job)

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
    prep_plan_store = InMemoryPrepPlanStore()
    launch_repository = InMemoryInterviewLaunchRepository()
    app.dependency_overrides[get_session_store] = lambda: store
    app.dependency_overrides[route_module.get_prep_plan_store] = lambda: prep_plan_store
    app.dependency_overrides[route_module.get_interview_launch_repository] = (
        lambda: launch_repository
    )
    route_module.get_report_job_store = lambda: job_store
    client = TestClient(app)
    client.practice_plan_store = prep_plan_store
    client.practice_launch_repository = launch_repository
    return client, store, llm, job_store


def teardown_function():
    app.dependency_overrides.clear()
    route_module.get_report_job_store = _ORIGINAL_GET_REPORT_JOB_STORE


def test_public_report_error_fallback_only_classifies_explicit_queue_failure():
    assert (
        route_module._public_report_error_code("report queue unavailable")
        == "report_enqueue_unavailable"
    )
    assert (
        route_module._public_report_error_code(
            "provider returned an unexpected queue-shaped payload"
        )
        == "report_generation_failed"
    )


def test_retry_exhaustion_is_explicitly_terminal_even_with_frontend_guidance():
    assert route_module._report_error_retryable("report_retry_exhausted") is False


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


def answer_all_questions(client: TestClient, session_id: str):
    responses = []
    for index in range(3):
        responses.append(
            client.post(
                f"/api/interviews/{session_id}/answer",
                json={"answer": f"Initial answer {index + 1}."},
            )
        )
        responses.append(
            client.post(
                f"/api/interviews/{session_id}/answer",
                json={"answer": f"Detailed follow-up answer {index + 1}."},
            )
        )
    return responses


def test_report_endpoint_returns_404_for_unknown_session():
    client, _, _, _ = make_client()

    response = client.get("/api/interviews/missing/report")

    assert response.status_code == 404


def test_get_question_evaluations_returns_saved_records():
    from app.services.question_evaluations import question_evaluation_from_feedback

    client, store, _, _ = make_client()
    session_id = start_interview(client)
    feedback = InterviewFeedback(
        question_id="q1",
        question_text="Introduce a backend project.",
        user_answer="The candidate built a backend cache service.",
        score=80,
        dimension_scores=make_dimension_scores(80),
        rationale="Covered the basic pattern.",
        critique="Needs more failure handling.",
        better_answer="Add consistency and retry details.",
        references=[],
    )
    store.save_question_evaluations(
        session_id,
        [question_evaluation_from_feedback(session_id=session_id, feedback=feedback)],
    )

    result = client.get(f"/api/interviews/{session_id}/question-evaluations")

    assert result.status_code == 200
    assert result.json()["items"][0]["question_id"] == "q1"
    assert result.json()["items"][0]["feedback"]["score"] == 80


def test_question_evaluations_endpoint_returns_404_for_unknown_session():
    client, _, _, _ = make_client()

    response = client.get("/api/interviews/missing/question-evaluations")

    assert response.status_code == 404
    assert response.json()["detail"] == "session not found"


def test_report_endpoint_rejects_active_interview():
    client, _, _, _ = make_client()
    session_id = start_interview(client)

    response = client.get(f"/api/interviews/{session_id}/report")

    assert response.status_code == 404


def test_report_pdf_endpoint_returns_404_for_unknown_session():
    client, _, _, _ = make_client()

    response = client.get("/api/interviews/missing/report.pdf")

    assert response.status_code == 404
    assert response.json()["detail"] == "session not found"


def test_report_pdf_endpoint_rejects_active_interview():
    client, _, _, _ = make_client()
    session_id = start_interview(client)

    response = client.get(f"/api/interviews/{session_id}/report.pdf")

    assert response.status_code == 409
    assert response.json()["detail"] == "interview is not finished"


def test_reports_endpoint_lists_completed_failed_and_processing_reports():
    client, store, _, _ = make_client()
    completed = start_interview(client)
    failed = start_interview(client)
    processing = start_interview(client)
    store.submit_answer(completed, "I designed the cache recovery path.")
    finish_session(store, completed)
    finish_session(store, failed)
    finish_session(store, processing)
    store.save_report(completed, make_report_model(completed, summary="Completed summary."))
    store.fail_report(failed, "llm timeout")
    store.mark_report_processing(processing)

    response = client.get("/api/reports")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 3
    assert [item["session_id"] for item in body["items"]] == [
        processing,
        failed,
        completed,
    ]
    assert [item["status"] for item in body["items"]] == [
        "processing",
        "failed",
        "completed",
    ]
    assert body["items"][0]["overall_score"] is None
    assert body["items"][0]["report_pdf_url"] is None
    assert body["items"][1]["error"] == "llm timeout"
    assert body["items"][2]["overall_score"] == 81
    assert body["items"][2]["summary"] == "Completed summary."
    assert body["items"][2]["answered_question_count"] == 1
    assert body["items"][2]["report_url"] == f"/api/interviews/{completed}/report"
    assert body["items"][2]["report_pdf_url"] == f"/api/interviews/{completed}/report.pdf"


@pytest.mark.parametrize(
    ("stored_path", "public_path"),
    [
        ("microbatch", "microbatch"),
        ("full_session", "full_session"),
        ("full_session_fallback", "full_session_fallback"),
        ("fallback_failed", None),
        (None, None),
    ],
)
def test_reports_endpoint_exposes_only_safe_joined_metadata(
    stored_path: str | None,
    public_path: str | None,
):
    client, store, _, _ = make_client()
    session_id = start_interview(client)
    state = store.get(session_id)
    state["status"] = "finished"
    state["current_index"] = len(state["plan"].questions)
    state["started_at"] = "2026-07-17T08:00:00Z"
    state["finished_at"] = "2026-07-17T08:01:05Z"
    store.mark_report_processing(session_id)
    store.update_report_progress(
        session_id,
        ReportProgress(
            stage="analyzing",
            percent=60,
            message="Analyzing interview evidence.",
            metadata={"report_path": stored_path} if stored_path is not None else {},
        ),
    )
    store.save_report(session_id, make_report_model(session_id))

    response = client.get("/api/reports")

    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["job_title"] == "Backend mock interview"
    assert item["job_tags"] == ["python", "redis"]
    assert item["question_count"] == 3
    assert item["answered_question_count"] == 0
    assert item["started_at"] == state["started_at"]
    assert item["duration_seconds"] == 65
    assert item["report_path"] == public_path
    assert "job_description" not in item
    assert "resume_text" not in item
    assert "messages" not in item


def test_reports_endpoint_omits_duration_without_finished_timestamp():
    client, store, _, _ = make_client()
    session_id = start_interview(client)
    finish_session(store, session_id)
    store.mark_report_processing(session_id)

    response = client.get("/api/reports")

    assert response.status_code == 200
    assert response.json()["items"][0]["duration_seconds"] is None


def test_reports_endpoint_filters_status_and_limit():
    client, store, _, _ = make_client()
    first = start_interview(client)
    second = start_interview(client)
    finish_session(store, first)
    finish_session(store, second)
    store.save_report(first, make_report_model(first, summary="First completed."))
    store.mark_report_processing(second)

    response = client.get("/api/reports?status=completed&limit=1")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["status_totals"] == {
        "all": 2,
        "processing": 1,
        "completed": 1,
        "failed": 0,
    }
    assert body["items"][0]["session_id"] == first
    assert body["items"][0]["status"] == "completed"
    assert body["items"][0]["summary"] == "First completed."


def test_reports_endpoint_returns_real_offset_page_and_total():
    client, store, _, _ = make_client()
    session_ids = [start_interview(client) for _ in range(3)]
    for index, session_id in enumerate(session_ids, start=1):
        finish_session(store, session_id)
        store.save_report(
            session_id,
            make_report_model(session_id, summary=f"Summary {index}"),
        )

    response = client.get("/api/reports?limit=1&offset=1")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 3
    assert body["limit"] == 1
    assert body["offset"] == 1
    assert len(body["items"]) == 1
    assert body["status_totals"] == {
        "all": 3,
        "processing": 0,
        "completed": 3,
        "failed": 0,
    }


def test_reports_endpoint_filters_query_and_days_before_pagination():
    client, store, _, _ = make_client()
    old_session = start_interview(client)
    recent_session = start_interview(client)
    for session_id in (old_session, recent_session):
        finish_session(store, session_id)
    store.save_report(
        old_session,
        make_report_model(old_session, summary="Legacy database interview."),
    )
    store.save_report(
        recent_session,
        make_report_model(recent_session, summary="Modern Redis interview."),
    )
    store._reports[old_session].created_at = "2020-01-01T00:00:00Z"
    store._reports[old_session].finished_at = "2020-01-01T00:00:00Z"

    query_response = client.get("/api/reports?query=modern%20redis")
    days_response = client.get("/api/reports?days=30")

    assert query_response.status_code == 200
    assert query_response.json()["total"] == 1
    assert query_response.json()["status_totals"]["all"] == 1
    assert query_response.json()["status_totals"]["completed"] == 1
    assert query_response.json()["items"][0]["session_id"] == recent_session
    assert days_response.status_code == 200
    assert days_response.json()["total"] == 1
    assert days_response.json()["items"][0]["session_id"] == recent_session


def test_reports_endpoint_rejects_invalid_status():
    client, _, _, _ = make_client()

    response = client.get("/api/reports?status=missing")

    assert response.status_code == 422
    assert response.json()["detail"] == "invalid status"


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


def test_completed_report_endpoint_returns_authoritative_reliability_object():
    client, store, _, _ = make_client()
    session_id = start_interview(client)
    responses = answer_all_questions(client, session_id)
    assert all(response.status_code == 200 for response in responses)
    store.save_report(session_id, make_report_model(session_id))

    response = client.get(f"/api/interviews/{session_id}/report")

    assert response.status_code == 200
    assert response.json()["reliability"] == {
        "planned_question_count": 3,
        "answered_question_count": 3,
        "skipped_question_count": 0,
        "unanswered_question_count": 0,
        "reviewed_answer_count": 0,
        "review_failed_answer_count": 0,
        "evidence_bound_question_count": 0,
        "degraded_question_count": 0,
        "generation_path": "mixed",
        "degraded_reasons": ["QUESTION_REVIEW_INCOMPLETE"],
        "score_applicability": "insufficient",
    }


def test_practice_plan_endpoint_returns_new_editable_plan_with_provenance():
    client, store, _, _ = make_client()
    session_id = start_interview(client)
    responses = answer_all_questions(client, session_id)
    assert all(response.status_code == 200 for response in responses)
    report = make_report_model(session_id)
    report = report.model_copy(
        update={
            "overall_dimension_scores": report.overall_dimension_scores.model_copy(
                update={"engineering": 55}
            )
        }
    )
    store.save_report(session_id, report)
    store.save_question_evaluations(
        session_id,
        [
            question_evaluation_from_feedback(
                session_id=session_id,
                feedback=report.feedbacks[0],
            )
        ],
    )
    client.practice_launch_repository.create_pending(
        plan_id="source-plan",
        command_id="source-command",
        consumed_plan_version=1,
        session_id=session_id,
        mappings=[
            {
                "plan_question_id": f"pq-source-{index}",
                "session_question_id": f"q{index}",
                "position": index,
                "kind": "technical",
            }
            for index in range(1, 4)
        ],
    )

    response = client.post(
        f"/api/interviews/{session_id}/practice-plan",
        json={
            "focus_dimension": "engineering",
            "session_question_ids": ["q1"],
            "mode": "targeted",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["state"] == "editable"
    assert body["plan_version"] == 1
    assert len(body["questions"]) == 3
    assert body["practice_provenance"] == {
        "source_session_id": session_id,
        "source_session_question_ids": ["q1"],
        "source_plan_question_ids": ["pq-source-1"],
        "source_report_id": session_id,
        "focus_dimension": "engineering",
    }


def test_practice_plan_endpoint_rejects_unfinished_report_without_creating_plan():
    client, _, _, _ = make_client()
    session_id = start_interview(client)

    response = client.post(
        f"/api/interviews/{session_id}/practice-plan",
        json={
            "focus_dimension": "engineering",
            "session_question_ids": ["q1"],
            "mode": "targeted",
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "PRACTICE_INTERVIEW_NOT_FINISHED"
    assert client.practice_plan_store._records == {}


@pytest.mark.parametrize(
    ("payload", "expected_code"),
    [
        (
            {
                "focus_dimension": "unsupported",
                "session_question_ids": ["q1"],
                "mode": "targeted",
            },
            "PRACTICE_INVALID_DIMENSION",
        ),
        (
            {
                "focus_dimension": "engineering",
                "session_question_ids": [],
                "mode": "targeted",
            },
            "PRACTICE_INVALID_QUESTION_IDS",
        ),
        (
            {
                "focus_dimension": "engineering",
                "session_question_ids": ["q1", "q1"],
                "mode": "targeted",
            },
            "PRACTICE_INVALID_QUESTION_IDS",
        ),
        (
            {
                "focus_dimension": "engineering",
                "session_question_ids": ["q1", "q2", "q3", "q4"],
                "mode": "targeted",
            },
            "PRACTICE_INVALID_QUESTION_IDS",
        ),
    ],
)
def test_practice_plan_endpoint_returns_stable_semantic_validation_errors(
    payload,
    expected_code,
):
    client, store, _, _ = make_client()
    session_id = start_interview(client)
    responses = answer_all_questions(client, session_id)
    assert all(response.status_code == 200 for response in responses)
    report = make_report_model(session_id)
    report = report.model_copy(
        update={
            "overall_dimension_scores": report.overall_dimension_scores.model_copy(
                update={"engineering": 55}
            )
        }
    )
    store.save_report(session_id, report)
    store.save_question_evaluations(
        session_id,
        [
            question_evaluation_from_feedback(
                session_id=session_id,
                feedback=report.feedbacks[0],
            )
        ],
    )

    response = client.post(
        f"/api/interviews/{session_id}/practice-plan",
        json=payload,
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == expected_code
    assert client.practice_plan_store._records == {}


def test_report_pdf_endpoint_rejects_processing_report():
    client, store, _, _ = make_client()
    session_id = start_interview(client)
    finish_session(store, session_id)
    store.mark_report_processing(session_id)

    response = client.get(f"/api/interviews/{session_id}/report.pdf")

    assert response.status_code == 409
    assert response.json()["detail"] == "report is not ready"


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
    ]
    assert body["attempt"] == 0
    assert body["heartbeat_at"] is None
    assert body["stalled"] is False
    assert body["rag"]["top_k"] == 5
    assert body["rag"]["source_types"] == ["theory", "expert_benchmark"]


def test_report_progress_endpoint_includes_progress_metadata():
    client, store, _, _ = make_client()
    session_id = start_interview(client)
    finish_session(store, session_id)
    store.mark_report_processing(session_id)
    store.update_report_progress(
        session_id,
        ReportProgress(
            stage="analyzing",
            percent=60,
            message="Reusing question-level review scores.",
            current_question_id="q1",
            metadata={
                "report_path": "microbatch",
                "microbatch_total_questions": 2,
                "microbatch_reused_questions": 1,
                "microbatch_rerun_questions": 1,
                "microbatch_failed_questions": 0,
            },
        ),
    )

    response = client.get(f"/api/interviews/{session_id}/report/progress")

    assert response.status_code == 200
    body = response.json()
    assert body["metadata"]["report_path"] == "microbatch"
    assert body["metadata"]["microbatch_rerun_questions"] == 1


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
    store.mark_report_processing(session_id)
    store.update_report_progress(
        session_id,
        ReportProgress(
            stage="completed",
            percent=100,
            message="Bound evidence report completed.",
            metadata={
                "report_path": "microbatch",
                "knowledge_path": "bound_evidence_reuse",
            },
        ),
    )
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
    assert body["events"] == []
    assert body["metadata"] == {
        "report_path": "microbatch",
        "knowledge_path": "bound_evidence_reuse",
    }


def test_report_pdf_endpoint_returns_pdf_for_completed_report():
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

    response = client.get(f"/api/interviews/{session_id}/report.pdf")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/pdf")
    assert "attachment; filename=" in response.headers["content-disposition"]
    assert response.content.startswith(b"%PDF")


def test_report_progress_endpoint_rejects_active_interview():
    client, _, _, _ = make_client()
    session_id = start_interview(client)

    response = client.get(f"/api/interviews/{session_id}/report/progress")

    assert response.status_code == 404
    assert response.json()["detail"] == "interview is not finished"


def test_failed_report_can_be_requeued():
    client, store, _, job_store = make_client()
    session_id = start_interview(client)
    finish_session(store, session_id)
    job_store.seed_job(session_id, status="failed")

    response = client.post(f"/api/interviews/{session_id}/report/requeue")

    assert response.status_code == 202
    assert response.json() == {
        "session_id": session_id,
        "report_job_id": f"job-{session_id}",
        "status": "queued",
        "attempt": 0,
        "recovered_from": "failed",
        "report_progress_url": f"/api/interviews/{session_id}/report/progress",
    }
    assert job_store.requeue_calls == [session_id]
    assert job_store.get_job_by_session(session_id)["replay_count"] == 1


def test_report_requeue_returns_404_for_unknown_session():
    client, _, _, _ = make_client()

    response = client.post("/api/interviews/missing/report/requeue")

    assert response.status_code == 404
    assert response.json() == {"detail": "interview session not found"}


def test_report_requeue_returns_404_when_job_is_missing():
    client, _, _, _ = make_client()
    session_id = start_interview(client)

    response = client.post(f"/api/interviews/{session_id}/report/requeue")

    assert response.status_code == 404
    assert response.json() == {"detail": "report job not found"}


def test_stale_processing_report_without_job_can_be_recovered_by_requeue():
    from datetime import datetime, timedelta, timezone

    client, store, _, job_store = make_client()
    session_id = start_interview(client)
    finish_session(store, session_id)
    store.mark_report_processing(session_id)
    store._reports[session_id] = store._reports[session_id].model_copy(
        update={
            "created_at": (
                datetime.now(timezone.utc) - timedelta(minutes=5)
            ).isoformat().replace("+00:00", "Z")
        }
    )

    response = client.post(f"/api/interviews/{session_id}/report/requeue")

    assert response.status_code == 202
    assert response.json()["status"] == "queued"
    assert response.json()["recovered_from"] == "orphaned"
    assert response.json()["report_job_id"] == "job-1"
    assert job_store.get_job_by_session(session_id)["status"] == "queued"


@pytest.mark.parametrize("status", ["queued", "retrying", "running"])
def test_report_requeue_rejects_queued_or_processing_jobs(status):
    client, _, _, job_store = make_client()
    session_id = start_interview(client)
    job_store.seed_job(session_id, status=status)

    response = client.post(f"/api/interviews/{session_id}/report/requeue")

    assert response.status_code == 409
    assert response.json() == {
        "detail": "report job is already queued or processing"
    }
    assert job_store.requeue_calls == []


def test_report_requeue_rejects_completed_job():
    client, _, _, job_store = make_client()
    session_id = start_interview(client)
    job_store.seed_job(session_id, status="completed")

    response = client.post(f"/api/interviews/{session_id}/report/requeue")

    assert response.status_code == 409
    assert response.json() == {"detail": "completed report cannot be requeued"}
    assert job_store.requeue_calls == []


def test_report_requeue_rejects_unknown_non_failed_job_state():
    client, _, _, job_store = make_client()
    session_id = start_interview(client)
    job_store.seed_job(session_id, status="cancelled")

    response = client.post(f"/api/interviews/{session_id}/report/requeue")

    assert response.status_code == 409
    assert response.json() == {"detail": "report job is not failed"}
    assert job_store.requeue_calls == []


def test_report_requeue_maps_status_race_to_stable_conflict():
    client, _, _, job_store = make_client()
    session_id = start_interview(client)
    job_store.seed_job(session_id, status="failed")
    job_store.raise_requeue_race = True

    response = client.post(f"/api/interviews/{session_id}/report/requeue")

    assert response.status_code == 409
    assert response.json() == {"detail": "report job is not failed"}


def test_second_report_requeue_returns_processing_conflict():
    client, store, _, job_store = make_client()
    session_id = start_interview(client)
    finish_session(store, session_id)
    job_store.seed_job(session_id, status="failed")

    first = client.post(f"/api/interviews/{session_id}/report/requeue")
    second = client.post(f"/api/interviews/{session_id}/report/requeue")

    assert first.status_code == 202
    assert second.status_code == 409
    assert second.json() == {
        "detail": "report job is already queued or processing"
    }
    assert job_store.requeue_calls == [session_id]


def test_report_requeue_returns_503_when_queue_is_unavailable():
    client, _, _, _ = make_client()
    route_module.get_report_job_store = lambda: (_ for _ in ()).throw(
        RuntimeError("database is unavailable")
    )
    session_id = start_interview(client)

    response = client.post(f"/api/interviews/{session_id}/report/requeue")

    assert response.status_code == 503
    assert response.json() == {"detail": "report queue is unavailable"}


def test_finished_answer_enqueues_report_generation_once_and_leaves_processing():
    client, store, llm, job_store = make_client()
    session_id = start_interview(client)

    responses = answer_all_questions(client, session_id)
    first_response = responses[0]
    second_response = responses[-1]

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


def test_finished_answer_fails_report_without_process_coupled_fallback_when_job_store_is_unavailable():
    client, store, llm, _ = make_client()
    route_module.get_report_job_store = lambda: (_ for _ in ()).throw(
        RuntimeError("POSTGRES_DSN is required to build report job store")
    )
    session_id = start_interview(client)

    responses = answer_all_questions(client, session_id)
    first_response = responses[0]
    second_response = responses[-1]

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    assert second_response.json()["status"] == "finished"
    assert llm.report_calls == 0
    record = store.get_report_record(session_id)
    assert record is not None
    assert record.status == "failed"
    assert record.error == "report queue unavailable"

    report_response = client.get(f"/api/interviews/{session_id}/report")
    assert report_response.status_code == 500
    assert report_response.json()["detail"] == "report queue unavailable"


def test_report_endpoint_returns_500_for_failed_report():
    client, store, _, _ = make_client()
    session_id = start_interview(client)
    finish_session(store, session_id)
    store.mark_report_processing(session_id)
    store.fail_report(session_id, "report generation timed out")

    response = client.get(f"/api/interviews/{session_id}/report")

    assert response.status_code == 500
    assert response.json()["detail"] == "report generation timed out"


def test_report_pdf_endpoint_rejects_failed_report():
    client, store, _, _ = make_client()
    session_id = start_interview(client)
    finish_session(store, session_id)
    store.mark_report_processing(session_id)
    store.fail_report(session_id, "report generation timed out")

    response = client.get(f"/api/interviews/{session_id}/report.pdf")

    assert response.status_code == 409
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


def test_report_endpoint_returns_quality_failure_detail():
    client, store, _, _ = make_client()
    session_id = start_interview(client)
    finish_session(store, session_id)
    store.mark_report_processing(session_id)
    store.fail_report(
        session_id,
        "runtime report quality check failed: summary must include Simplified Chinese text",
    )

    response = client.get(f"/api/interviews/{session_id}/report")

    assert response.status_code == 500
    assert (
        response.json()["detail"]
        == "runtime report quality check failed: summary must include Simplified Chinese text"
    )


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
