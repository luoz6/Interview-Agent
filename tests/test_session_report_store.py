from contextlib import contextmanager

import pytest

from app.services.prep import InterviewPlan, InterviewQuestion
from app.services.postgres_session import PostgresInterviewSessionStore
from app.services.report import (
    DimensionScores,
    InterviewFeedback,
    InterviewReport,
)
from app.services.session import InterviewSessionStore


class _CapturingCursor:
    def __init__(self):
        self.query = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, query, _params):
        self.query = query

    def fetchall(self):
        return []


class _CapturingConnection:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor


class _CapturingProvider:
    def __init__(self):
        self.cursor = _CapturingCursor()

    @contextmanager
    def connection(self):
        yield _CapturingConnection(self.cursor)


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


def test_postgres_report_list_sql_binds_answer_message_table_without_database():
    provider = _CapturingProvider()
    store = object.__new__(PostgresInterviewSessionStore)
    store._connection_provider = provider
    store.reports_table = "contract_reports"
    store.sessions_table = "contract_sessions"
    store.messages_table = "contract_messages"

    reports = store.list_reports(limit=10)

    assert reports == []
    assert "contract_messages" in repr(provider.cursor.query)


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


def test_stale_failure_does_not_overwrite_completed_report():
    store = InterviewSessionStore(llm=FakeInterviewLLM())
    session = start_session(store)
    finish_session(store, session.session_id)
    store.mark_report_processing(session.session_id)
    report = make_report(session.session_id)
    store.save_report(session.session_id, report)

    store.fail_report(session.session_id, "stale worker failure")

    record = store.get_report_record(session.session_id)
    state = store.get(session.session_id)
    assert record.status == "completed"
    assert record.report == report
    assert state["review_status"] == "completed"
    assert state["phase_status"] == "completed"


def test_duplicate_failure_retains_original_terminal_record():
    store = InterviewSessionStore(llm=FakeInterviewLLM())
    session = start_session(store)
    finish_session(store, session.session_id)
    store.mark_report_processing(session.session_id)
    store.fail_report(session.session_id, "first failure")
    first = store.get_report_record(session.session_id)

    store.fail_report(session.session_id, "late duplicate failure")

    record = store.get_report_record(session.session_id)
    assert record == first
    assert record.error == "first failure"


def test_list_reports_returns_completed_failed_and_processing_records():
    store = InterviewSessionStore(llm=FakeInterviewLLM())
    first = start_session(store)
    second = start_session(store)
    third = start_session(store)
    finish_session(store, first.session_id)
    finish_session(store, second.session_id)
    finish_session(store, third.session_id)
    store.get(third.session_id)["messages"] = [
        {
            "role": "candidate",
            "content": "I built a cache service.",
            "question_id": "q1",
        }
    ]

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
    processing_state = store.get(third.session_id)
    assert reports[0]["session_summary"] == {
        "job_title": processing_state["plan"].title,
        "job_tags": processing_state["job_tags"],
        "question_count": len(processing_state["plan"].questions),
        "answered_question_count": 1,
        "started_at": processing_state["started_at"],
        "finished_at": processing_state["finished_at"],
    }
    assert "job_description" not in reports[0]["session_summary"]
    assert "resume_text" not in reports[0]["session_summary"]
    assert "messages" not in reports[0]["session_summary"]


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
