import os
from uuid import uuid4

import pytest

from app.services.postgres_session import PostgresInterviewSessionStore
from app.services.prep import InterviewPlan, InterviewQuestion
from app.services.report import (
    DimensionScores,
    InterviewFeedback,
    InterviewReport,
    ReportProgress,
)


pytestmark = pytest.mark.pg_runtime


def require_dsn():
    dsn = os.getenv("POSTGRES_DSN")
    if not dsn:
        pytest.skip("POSTGRES_DSN is required for pg_runtime tests")
    return dsn


def make_table_prefix():
    return "test_runtime_" + uuid4().hex[:12]


def test_schema_initializes_runtime_tables():
    store = PostgresInterviewSessionStore(
        dsn=require_dsn(),
        table_prefix=make_table_prefix(),
    )

    tables = store.list_runtime_tables()

    assert set(tables) == {
        store.sessions_table,
        store.messages_table,
        store.reports_table,
        store.question_evaluations_table,
    }


def make_plan():
    return InterviewPlan(
        title="Backend Interview",
        questions=[
            InterviewQuestion(
                id="q1",
                kind="project",
                prompt="Describe your backend project.",
                focus="Project depth",
            )
        ],
    )


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
                question_text="Describe your backend project.",
                user_answer="I built a FastAPI API.",
                score=80,
                dimension_scores=make_dimension_scores(),
                rationale="The answer covered the project shape.",
                critique="Needs more failure-mode detail.",
                better_answer="Explain traffic, storage, caching, and failure handling.",
                references=[],
            )
        ],
    )


def test_started_session_survives_store_reinstantiation():
    dsn = require_dsn()
    table_prefix = make_table_prefix()
    store = PostgresInterviewSessionStore(dsn=dsn, table_prefix=table_prefix)

    turn = store.start(
        make_plan(),
        job_description="Python backend role",
        resume_text="Built FastAPI services",
        job_tags=["python", "fastapi"],
    )

    recovered_store = PostgresInterviewSessionStore(dsn=dsn, table_prefix=table_prefix)
    state = recovered_store.get(turn.session_id)

    assert state["session_id"] == turn.session_id
    assert state["plan"].title == "Backend Interview"
    assert state["messages"][0]["role"] == "interviewer"
    assert state["messages"][0]["content"] == "Describe your backend project."
    assert state["job_tags"] == ["python", "fastapi"]


def test_snapshot_survives_store_reinstantiation():
    dsn = require_dsn()
    table_prefix = make_table_prefix()
    store = PostgresInterviewSessionStore(dsn=dsn, table_prefix=table_prefix)

    turn = store.start(
        make_plan(),
        job_description="Python backend role",
        resume_text="Built FastAPI services",
        job_tags=["python", "fastapi"],
    )

    recovered_store = PostgresInterviewSessionStore(dsn=dsn, table_prefix=table_prefix)
    snapshot = recovered_store.snapshot(turn.session_id)

    assert snapshot["session_id"] == turn.session_id
    assert snapshot["status"] == "active"
    assert snapshot["job_tags"] == ["python", "fastapi"]
    assert snapshot["current_question"]["id"] == "q1"
    assert snapshot["questions"][0]["state"] == "current"
    assert snapshot["messages"][0]["content"] == "Describe your backend project."


def test_skip_persists_next_question_snapshot():
    dsn = require_dsn()
    table_prefix = make_table_prefix()
    store = PostgresInterviewSessionStore(dsn=dsn, table_prefix=table_prefix)

    turn = store.start(
        InterviewPlan(
            title="Two question interview",
            questions=[
                InterviewQuestion(
                    id="q1",
                    kind="project",
                    prompt="Describe a backend project.",
                    focus="project",
                ),
                InterviewQuestion(
                    id="q2",
                    kind="technical",
                    prompt="Explain Redis consistency.",
                    focus="redis",
                ),
            ],
        ),
        job_description="Python backend role",
        resume_text="Built FastAPI services",
        job_tags=["python", "redis"],
    )

    store.skip(turn.session_id)

    recovered_store = PostgresInterviewSessionStore(dsn=dsn, table_prefix=table_prefix)
    snapshot = recovered_store.snapshot(turn.session_id)

    assert snapshot["status"] == "active"
    assert snapshot["current_question"]["id"] == "q2"
    assert snapshot["questions"][0]["state"] == "skipped"
    assert snapshot["questions"][1]["state"] == "current"
    assert snapshot["messages"][-1]["content"] == "Explain Redis consistency."


def test_skip_metadata_survives_store_reinstantiation():
    dsn = require_dsn()
    table_prefix = make_table_prefix()
    store = PostgresInterviewSessionStore(dsn=dsn, table_prefix=table_prefix)

    turn = store.start(
        make_plan(),
        job_description="Backend role using Python and Redis.",
        resume_text="Built Redis APIs.",
        job_tags=["python", "redis"],
    )
    store.skip(turn.session_id)

    recovered_store = PostgresInterviewSessionStore(dsn=dsn, table_prefix=table_prefix)
    state = recovered_store.get(turn.session_id)
    snapshot = recovered_store.snapshot(turn.session_id)

    assert state["skipped_question_ids"] == ["q1"]
    assert state["started_at"]
    assert state["finished_at"] is not None
    assert snapshot["questions"][0]["state"] == "skipped"
    assert snapshot["skipped_questions"] == 1


def test_submit_answer_persists_candidate_and_followup_messages():
    dsn = require_dsn()
    table_prefix = make_table_prefix()
    store = PostgresInterviewSessionStore(dsn=dsn, table_prefix=table_prefix)
    turn = store.start(
        make_plan(),
        job_description="Python backend role",
        resume_text="Built FastAPI services",
        job_tags=["python", "fastapi"],
    )

    answered = store.submit_answer(turn.session_id, "I built a FastAPI API.")

    recovered_store = PostgresInterviewSessionStore(dsn=dsn, table_prefix=table_prefix)
    state = recovered_store.get(turn.session_id)
    assert answered.follow_up is not None
    assert [message["role"] for message in state["messages"]] == [
        "interviewer",
        "candidate",
        "interviewer",
    ]
    assert state["messages"][1]["content"] == "I built a FastAPI API."
    assert state["messages"][2]["content"] == answered.follow_up


def test_streaming_prepare_and_complete_are_persisted_once():
    dsn = require_dsn()
    table_prefix = make_table_prefix()
    store = PostgresInterviewSessionStore(dsn=dsn, table_prefix=table_prefix)
    turn = store.start(
        make_plan(),
        job_description="Python backend role",
        resume_text="Built FastAPI services",
        job_tags=["python", "fastapi"],
    )

    prepared = store.prepare_streaming_answer(turn.session_id, "I built APIs.")
    assert prepared.stream_follow_up is True

    store.complete_streaming_answer(
        turn.session_id,
        follow_up_text="Which failure mode did you handle?",
    )
    store.complete_streaming_answer(
        turn.session_id,
        follow_up_text="Which failure mode did you handle?",
    )

    recovered = PostgresInterviewSessionStore(
        dsn=dsn,
        table_prefix=table_prefix,
    ).get(turn.session_id)

    assert [message["role"] for message in recovered["messages"]] == [
        "interviewer",
        "candidate",
        "interviewer",
    ]
    assert recovered["messages"][-1]["content"] == "Which failure mode did you handle?"


def finish_session(store, session_id):
    store.submit_answer(session_id, "First answer.")
    store.submit_answer(session_id, "Second answer.")


def test_report_lifecycle_survives_store_reinstantiation():
    dsn = require_dsn()
    table_prefix = make_table_prefix()
    store = PostgresInterviewSessionStore(dsn=dsn, table_prefix=table_prefix)
    turn = store.start(
        make_plan(),
        job_description="Python backend role",
        resume_text="Built FastAPI services",
        job_tags=["python", "fastapi"],
    )
    finish_session(store, turn.session_id)

    assert store.mark_report_processing(turn.session_id) is True
    store.update_report_progress(
        turn.session_id,
        ReportProgress(
            stage="analyzing",
            percent=60,
            message="Analyzing answers.",
            current_question_id="q1",
        ),
    )

    recovered_store = PostgresInterviewSessionStore(dsn=dsn, table_prefix=table_prefix)
    record = recovered_store.get_report_record(turn.session_id)

    assert record is not None
    assert record.status == "processing"
    assert record.progress is not None
    assert record.progress.percent == 60

    recovered_store.fail_report(turn.session_id, "retrieval unavailable")
    failed = PostgresInterviewSessionStore(
        dsn=dsn,
        table_prefix=table_prefix,
    ).get_report_record(turn.session_id)

    assert failed is not None
    assert failed.status == "failed"
    assert failed.error == "retrieval unavailable"


def test_list_reports_survives_store_reinstantiation():
    dsn = require_dsn()
    table_prefix = make_table_prefix()
    store = PostgresInterviewSessionStore(dsn=dsn, table_prefix=table_prefix)
    completed = store.start(
        make_plan(),
        job_description="Python backend role",
        resume_text="Built FastAPI services",
        job_tags=["python", "fastapi"],
    )
    processing = store.start(
        make_plan(),
        job_description="Python backend role",
        resume_text="Built FastAPI services",
        job_tags=["python", "fastapi"],
    )
    finish_session(store, completed.session_id)
    finish_session(store, processing.session_id)

    store.mark_report_processing(completed.session_id)
    store.save_report(completed.session_id, make_report(completed.session_id))
    store.mark_report_processing(processing.session_id)

    recovered_store = PostgresInterviewSessionStore(dsn=dsn, table_prefix=table_prefix)
    reports = recovered_store.list_reports(limit=10)
    completed_reports = recovered_store.list_reports(status="completed", limit=10)

    assert [item["session_id"] for item in reports] == [
        processing.session_id,
        completed.session_id,
    ]
    assert [item["record"].status for item in reports] == [
        "processing",
        "completed",
    ]
    assert len(completed_reports) == 1
    assert completed_reports[0]["session_id"] == completed.session_id
    assert completed_reports[0]["record"].report.summary == "Solid interview."


def test_postgres_store_persists_question_evaluations():
    from app.services.question_evaluations import question_evaluation_from_feedback

    dsn = require_dsn()
    table_prefix = make_table_prefix()
    store = PostgresInterviewSessionStore(dsn=dsn, table_prefix=table_prefix)
    turn = store.start(
        make_plan(),
        job_description="Python backend role",
        resume_text="Built FastAPI services",
        job_tags=["python", "fastapi"],
    )
    feedback = InterviewFeedback(
        question_id="q1",
        question_text="Describe your backend project.",
        user_answer="I built a FastAPI API.",
        score=78,
        dimension_scores=make_dimension_scores(78),
        rationale="The answer gave implementation context.",
        critique="Business impact was thin.",
        better_answer="Tie the API work to latency and reliability outcomes.",
        references=[],
    )

    store.save_question_evaluations(
        turn.session_id,
        [question_evaluation_from_feedback(session_id=turn.session_id, feedback=feedback)],
    )

    recovered_store = PostgresInterviewSessionStore(dsn=dsn, table_prefix=table_prefix)
    saved = recovered_store.list_question_evaluations(turn.session_id)
    assert len(saved) == 1
    assert saved[0].question_id == "q1"
    assert saved[0].answer_state == "answered"
    assert saved[0].feedback.score == 78


def test_submit_answer_appends_new_messages_without_rewriting_existing_rows():
    dsn = require_dsn()
    table_prefix = make_table_prefix()
    store = PostgresInterviewSessionStore(dsn=dsn, table_prefix=table_prefix)
    turn = store.start(
        make_plan(),
        job_description="Python backend role",
        resume_text="Built FastAPI services",
        job_tags=["python", "fastapi"],
    )

    first_snapshot = store.list_messages(turn.session_id)
    assert len(first_snapshot) == 1
    first_id = first_snapshot[0]["id"]

    store.submit_answer(turn.session_id, "I built a FastAPI API.")

    second_snapshot = store.list_messages(turn.session_id)

    assert len(second_snapshot) == 3
    assert second_snapshot[0]["id"] == first_id
    assert second_snapshot[0]["sequence_no"] == 1
    assert second_snapshot[1]["sequence_no"] == 2
    assert second_snapshot[2]["sequence_no"] == 3
