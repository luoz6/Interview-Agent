import pytest
from pydantic import ValidationError

from app.services.report import InterviewFeedback, InterviewReport, ReportRecord


def make_feedback(score: int = 82) -> InterviewFeedback:
    return InterviewFeedback(
        question_id="q1",
        question_text="Please introduce a backend project.",
        user_answer="The candidate described a FastAPI cache project.",
        score=score,
        critique="The answer missed concrete business metrics.",
        better_answer=(
            "I built a FastAPI service for order lookup, reduced repeated "
            "database reads with Redis, and measured latency before and after "
            "the change."
        ),
    )


def test_interview_feedback_validates_score_range():
    assert make_feedback(score=100).score == 100

    with pytest.raises(ValidationError):
        make_feedback(score=101)

    with pytest.raises(ValidationError):
        make_feedback(score=-1)


def test_interview_report_contains_completed_status():
    report = InterviewReport(
        session_id="s1",
        overall_score=82,
        summary="Solid fundamentals with missing result metrics.",
        highlights=["Explained the project context"],
        feedbacks=[make_feedback()],
    )

    assert report.status == "completed"
    assert report.is_fallback is False
    assert report.feedbacks[0].question_id == "q1"


def test_report_requires_one_to_three_highlights():
    with pytest.raises(ValidationError):
        InterviewReport(
            session_id="s1",
            overall_score=82,
            summary="No highlights should fail.",
            highlights=[],
            feedbacks=[make_feedback()],
        )

    with pytest.raises(ValidationError):
        InterviewReport(
            session_id="s1",
            overall_score=82,
            summary="Too many highlights should fail.",
            highlights=["a", "b", "c", "d"],
            feedbacks=[make_feedback()],
        )


def test_report_record_states():
    completed_report = InterviewReport(
        session_id="s1",
        overall_score=82,
        summary="Solid answer.",
        highlights=["Clear context"],
        feedbacks=[make_feedback()],
    )

    processing = ReportRecord(status="processing")
    completed = ReportRecord(status="completed", report=completed_report)
    failed = ReportRecord(status="failed", error="llm timeout")

    assert processing.report is None
    assert completed.report is not None
    assert failed.error == "llm timeout"


def test_report_record_rejects_invalid_state_combinations():
    with pytest.raises(ValidationError):
        ReportRecord(status="completed")

    with pytest.raises(ValidationError):
        ReportRecord(status="failed")

    with pytest.raises(ValidationError):
        ReportRecord(status="processing", error="should not exist")
