from app.graphs.interview_state import build_initial_state
from app.services.prep import InterviewPlan, InterviewQuestion
from app.services.report import (
    DimensionScores,
    FeedbackReference,
    InterviewFeedback,
    InterviewReport,
    ReportProgress,
    ReportRecord,
)
from app.services.session_serialization import (
    message_to_row,
    report_record_from_row,
    report_record_to_row,
    session_row_from_state,
    state_from_rows,
)


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


def make_state():
    return build_initial_state(
        session_id="s1",
        plan=make_plan(),
        job_description="Python backend role",
        resume_text="Built FastAPI services",
        job_tags=["python", "fastapi"],
    )


def make_report_record():
    report = InterviewReport(
        session_id="s1",
        overall_score=80,
        overall_dimension_scores=DimensionScores(
            breadth=80,
            depth=78,
            architecture=75,
            engineering=82,
            communication=84,
        ),
        summary="Solid backend project explanation.",
        highlights=["Clear project context"],
        feedbacks=[
            InterviewFeedback(
                question_id="q1",
                question_text="Describe your backend project.",
                user_answer="I built a FastAPI service.",
                score=80,
                dimension_scores=DimensionScores(
                    breadth=80,
                    depth=78,
                    architecture=75,
                    engineering=82,
                    communication=84,
                ),
                rationale="The answer covered project context and implementation.",
                critique="Failure modes need more detail.",
                better_answer="Explain traffic, storage, cache, failure handling, and tradeoffs.",
                references=[
                    FeedbackReference(
                        chunk_id="fastapi_backend",
                        title="FastAPI Backend",
                        source_type="expert_benchmark",
                        excerpt="High quality answers include API boundaries and failure handling.",
                    )
                ],
            )
        ],
    )
    return ReportRecord(status="completed", report=report)


def test_state_round_trips_from_session_and_message_rows():
    state = make_state()
    session_row = session_row_from_state(state)
    message_rows = [
        message_to_row("s1", index + 1, message)
        for index, message in enumerate(state["messages"])
    ]

    restored = state_from_rows(session_row, message_rows)

    assert restored["session_id"] == "s1"
    assert restored["plan"].questions[0].prompt == "Describe your backend project."
    assert restored["messages"] == state["messages"]
    assert restored["job_tags"] == ["python", "fastapi"]


def test_session_serialization_preserves_skip_and_timing_metadata():
    state = build_initial_state(
        session_id="s1",
        plan=make_plan(),
        job_description="Backend role",
        resume_text="Backend resume",
        job_tags=["python"],
    )
    state["skipped_question_ids"] = ["q1"]
    state["finished_at"] = "2026-07-04T10:00:00Z"

    row = session_row_from_state(state)
    restored = state_from_rows(row, [])

    assert row["skipped_question_ids"] == ["q1"]
    assert row["started_at"] == state["started_at"]
    assert row["finished_at"] == "2026-07-04T10:00:00Z"
    assert restored["skipped_question_ids"] == ["q1"]
    assert restored["started_at"] == state["started_at"]
    assert restored["finished_at"] == "2026-07-04T10:00:00Z"


def test_session_serialization_round_trips_orchestration_metadata():
    state = make_state()
    state["phase"] = "review"
    state["phase_status"] = "completed"
    state["review_status"] = "completed"
    state["state_version"] = 6
    state["checkpoint_version"] = 6
    state["last_checkpoint_at"] = "2026-07-08T10:00:00Z"
    state["last_command_id"] = "cmd-2"

    row = session_row_from_state(state)
    restored = state_from_rows(row, [])

    assert row["phase"] == "review"
    assert row["phase_status"] == "completed"
    assert row["review_status"] == "completed"
    assert row["state_version"] == 6
    assert row["checkpoint_version"] == 6
    assert row["last_checkpoint_at"] == "2026-07-08T10:00:00Z"
    assert row["last_command_id"] == "cmd-2"
    assert restored["phase"] == "review"
    assert restored["phase_status"] == "completed"
    assert restored["review_status"] == "completed"
    assert restored["state_version"] == 6
    assert restored["checkpoint_version"] == 6
    assert restored["last_checkpoint_at"] == "2026-07-08T10:00:00Z"
    assert restored["last_command_id"] == "cmd-2"


def test_report_record_round_trips_from_row():
    record = make_report_record()
    row = report_record_to_row(record)

    restored = report_record_from_row(row)

    assert restored.status == "completed"
    assert restored.report is not None
    assert restored.report.overall_score == 80
    assert restored.report.feedbacks[0].references[0].chunk_id == "fastapi_backend"


def test_report_record_round_trips_lifecycle_timestamps():
    report = make_report_record()
    record = ReportRecord(
        status="completed",
        report=report.report,
        created_at="2026-07-04T10:00:00Z",
        finished_at="2026-07-04T10:02:00Z",
    )

    row = report_record_to_row(record)
    restored = report_record_from_row(row)

    assert row["created_at"] == "2026-07-04T10:00:00Z"
    assert row["finished_at"] == "2026-07-04T10:02:00Z"
    assert restored.created_at == "2026-07-04T10:00:00Z"
    assert restored.finished_at == "2026-07-04T10:02:00Z"


def test_processing_report_record_round_trips_from_row():
    record = ReportRecord(
        status="processing",
        progress=ReportProgress(
            stage="retrieving",
            percent=20,
            message="Retrieving references.",
        ),
    )
    row = report_record_to_row(record)

    restored = report_record_from_row(row)

    assert restored.status == "processing"
    assert restored.progress is not None
    assert restored.progress.percent == 20
