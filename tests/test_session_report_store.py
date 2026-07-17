import pytest

from app.services.prep import InterviewPlan, InterviewQuestion
from app.services.report import (
    DimensionScores,
    InterviewFeedback,
    InterviewReport,
)
from app.services.session import InterviewSessionStore


class FakeInterviewLLM:
    def generate_plan(self, job_description: str, resume_text: str) -> InterviewPlan:
        return InterviewPlan(
            title="Backend mock interview",
            questions=[
                InterviewQuestion(
                    id="q1",
                    kind="project",
                    prompt="Introduce a project.",
                    focus="project depth",
                )
            ],
        )

    def generate_followup(self, context: list[dict[str, str]]) -> str:
        return "Please go deeper."

    def generate_report(
        self,
        plan: InterviewPlan,
        evaluation_items: list[dict],
        session_id: str,
    ) -> InterviewReport:
        raise AssertionError("Session store tests do not generate reports")


def make_plan() -> InterviewPlan:
    return FakeInterviewLLM().generate_plan("backend role", "backend resume")


def make_dimension_scores(score: int = 80) -> DimensionScores:
    return DimensionScores(
        breadth=score,
        depth=score,
        architecture=score,
        engineering=score,
        communication=score,
    )


def make_report(session_id: str) -> InterviewReport:
    return InterviewReport(
        session_id=session_id,
        overall_score=80,
        overall_dimension_scores=make_dimension_scores(),
        summary="Solid interview.",
        highlights=["Explained project context"],
        feedbacks=[
            InterviewFeedback(
                question_id="q1",
                question_text="Introduce a project.",
                user_answer="I built a cache service.",
                score=80,
                dimension_scores=make_dimension_scores(),
                rationale="The answer covered the implementation but missed business impact.",
                critique="Needs clearer business impact.",
                better_answer="I built a cache service that reduced p95 latency.",
                references=[],
            )
        ],
    )


def start_session(store: InterviewSessionStore):
    return store.start(
        make_plan(),
        job_description="Backend role using Python and Redis.",
        resume_text="Built a Python API with Redis.",
        job_tags=["python", "redis"],
    )


def finish_session(store: InterviewSessionStore, session_id: str) -> None:
    state = store.get(session_id)
    state["status"] = "finished"
    state["current_index"] = len(state["plan"].questions)


def test_mark_report_processing_requires_finished_session():
    store = InterviewSessionStore(llm=FakeInterviewLLM())
    session = start_session(store)

    with pytest.raises(ValueError, match="interview is not finished"):
        store.mark_report_processing(session.session_id)


def test_mark_report_processing_is_idempotent_after_first_success():
    store = InterviewSessionStore(llm=FakeInterviewLLM())
    session = start_session(store)
    finish_session(store, session.session_id)

    assert store.mark_report_processing(session.session_id) is True
    assert store.mark_report_processing(session.session_id) is False
    record = store.get_report_record(session.session_id)
    assert record.status == "processing"
    assert record.progress.stage == "retrieving"
    assert record.progress.percent == 20
    assert record.created_at
    assert record.finished_at is None


def test_store_saves_completed_report_record():
    store = InterviewSessionStore(llm=FakeInterviewLLM())
    session = start_session(store)
    finish_session(store, session.session_id)
    store.mark_report_processing(session.session_id)

    report = make_report(session.session_id)
    store.save_report(session.session_id, report)

    record = store.get_report_record(session.session_id)
    assert record.status == "completed"
    assert record.report == report
    assert record.error is None
    assert record.created_at
    assert record.finished_at


def test_store_saves_failed_report_record():
    store = InterviewSessionStore(llm=FakeInterviewLLM())
    session = start_session(store)
    finish_session(store, session.session_id)
    store.mark_report_processing(session.session_id)

    store.fail_report(session.session_id, "llm timeout")

    record = store.get_report_record(session.session_id)
    assert record.status == "failed"
    assert record.error == "llm timeout"
    assert record.report is None
    assert record.created_at
    assert record.finished_at


def test_list_reports_returns_completed_failed_and_processing_records():
    store = InterviewSessionStore(llm=FakeInterviewLLM())
    first = start_session(store)
    second = start_session(store)
    third = start_session(store)
    finish_session(store, first.session_id)
    finish_session(store, second.session_id)
    finish_session(store, third.session_id)

    store.mark_report_processing(first.session_id)
    store.save_report(first.session_id, make_report(first.session_id))
    store.mark_report_processing(second.session_id)
    store.fail_report(second.session_id, "llm timeout")
    store.mark_report_processing(third.session_id)

    reports = store.list_reports()

    assert [item["session_id"] for item in reports] == [
        third.session_id,
        second.session_id,
        first.session_id,
    ]
    assert [item["record"].status for item in reports] == [
        "processing",
        "failed",
        "completed",
    ]


def test_list_reports_filters_status_and_limit():
    store = InterviewSessionStore(llm=FakeInterviewLLM())
    first = start_session(store)
    second = start_session(store)
    finish_session(store, first.session_id)
    finish_session(store, second.session_id)
    store.mark_report_processing(first.session_id)
    store.save_report(first.session_id, make_report(first.session_id))
    store.mark_report_processing(second.session_id)

    reports = store.list_reports(status="completed", limit=1)

    assert len(reports) == 1
    assert reports[0]["session_id"] == first.session_id
    assert reports[0]["record"].status == "completed"


def test_report_methods_reject_unknown_session():
    store = InterviewSessionStore(llm=FakeInterviewLLM())
    report = make_report("missing")

    with pytest.raises(ValueError, match="session not found"):
        store.get_report_record("missing")
    with pytest.raises(ValueError, match="session not found"):
        store.mark_report_processing("missing")
    with pytest.raises(ValueError, match="session not found"):
        store.save_report("missing", report)
    with pytest.raises(ValueError, match="session not found"):
        store.fail_report("missing", "no session")
