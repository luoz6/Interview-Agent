import pytest
from pydantic import ValidationError

from app.services.report import (
    DimensionScores,
    FeedbackReference,
    InterviewFeedback,
    InterviewReport,
    ReportProgress,
    ReportRecord,
)


def make_dimension_scores(score: int = 82) -> DimensionScores:
    return DimensionScores(
        breadth=score,
        depth=score,
        architecture=score,
        engineering=score,
        communication=score,
    )


def make_reference() -> FeedbackReference:
    return FeedbackReference(
        chunk_id="redis-1",
        title="Redis cache consistency",
        source_type="theory",
        excerpt="Delete cache after database update and handle race conditions.",
    )


def make_feedback(score: int = 82) -> InterviewFeedback:
    return InterviewFeedback(
        question_id="q1",
        question_text="Please introduce a backend project.",
        user_answer="The candidate described a FastAPI cache project.",
        score=score,
        dimension_scores=make_dimension_scores(score),
        rationale="The answer covered the cache strategy but missed concrete metrics.",
        critique="The answer missed measurable business results.",
        better_answer=(
            "I built a FastAPI service for hot record lookup, reduced repeated "
            "database reads with Redis, and measured p95 latency before and "
            "after the change."
        ),
        references=[make_reference()],
    )


def test_dimension_scores_validate_range():
    assert make_dimension_scores(100).depth == 100

    with pytest.raises(ValidationError):
        DimensionScores(
            breadth=101,
            depth=80,
            architecture=80,
            engineering=80,
            communication=80,
        )


def test_interview_feedback_requires_dimension_scores_and_references():
    feedback = make_feedback()

    assert feedback.dimension_scores.depth == 82
    assert feedback.references[0].chunk_id == "redis-1"


def test_interview_report_contains_overall_dimension_scores():
    report = InterviewReport(
        session_id="s1",
        overall_score=82,
        overall_dimension_scores=make_dimension_scores(),
        summary="Solid fundamentals with missing result metrics.",
        highlights=["Explained the project context"],
        feedbacks=[make_feedback()],
    )

    assert report.status == "completed"
    assert report.overall_dimension_scores.communication == 82
    assert report.is_fallback is False
    assert report.feedbacks[0].answer_state == "answered"


def test_report_progress_validates_percent_and_stage():
    progress = ReportProgress(
        stage="retrieving",
        percent=20,
        message="Retrieving Redis references.",
        current_question_id=None,
    )
    assert progress.percent == 20

    with pytest.raises(ValidationError):
        ReportProgress(stage="retrieving", percent=101, message="bad")


def test_report_record_accepts_processing_with_progress():
    report = InterviewReport(
        session_id="s1",
        overall_score=82,
        overall_dimension_scores=make_dimension_scores(),
        summary="Solid answer.",
        highlights=["Clear context"],
        feedbacks=[make_feedback()],
    )

    processing = ReportRecord(
        status="processing",
        progress=ReportProgress(stage="retrieving", percent=20, message="Loading"),
    )
    completed = ReportRecord(status="completed", report=report)
    failed = ReportRecord(status="failed", error="pgvector unavailable")

    assert processing.progress is not None
    assert completed.report is not None
    assert failed.error == "pgvector unavailable"


def test_report_record_rejects_invalid_state_combinations():
    with pytest.raises(ValidationError):
        ReportRecord(status="processing")

    with pytest.raises(ValidationError):
        ReportRecord(status="completed")

    with pytest.raises(ValidationError):
        ReportRecord(status="failed")
