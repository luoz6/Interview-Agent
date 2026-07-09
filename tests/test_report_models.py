from datetime import datetime

import pytest
from pydantic import ValidationError

from app.services.report import (
    DimensionScores,
    FeedbackReference,
    InterviewFeedback,
    InterviewReport,
    ReportProgress,
    ReportQualityFailed,
    ReportRecord,
    ReportGenerationFailed,
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


def test_report_progress_accepts_metadata_for_observability():
    progress = ReportProgress(
        stage="analyzing",
        percent=60,
        message="Reusing question reviews.",
        current_question_id="q1",
        metadata={
            "report_path": "microbatch",
            "microbatch_total_questions": 2,
            "microbatch_reused_questions": 1,
            "microbatch_rerun_questions": 1,
            "microbatch_failed_questions": 0,
        },
    )

    assert progress.metadata["report_path"] == "microbatch"
    assert progress.metadata["microbatch_rerun_questions"] == 1
    assert progress.model_dump()["metadata"]["microbatch_total_questions"] == 2


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


def test_report_record_defaults_created_at_and_open_finished_at():
    record = ReportRecord(
        status="processing",
        progress=ReportProgress(stage="retrieving", percent=20, message="Loading"),
    )

    assert record.created_at
    assert record.finished_at is None
    datetime.fromisoformat(record.created_at.replace("Z", "+00:00"))


def test_report_record_completed_accepts_finished_at():
    record = ReportRecord(
        status="completed",
        report=InterviewReport(
            session_id="s1",
            overall_score=82,
            overall_dimension_scores=make_dimension_scores(),
            summary="Solid answer.",
            highlights=["Clear context"],
            feedbacks=[make_feedback()],
        ),
        created_at="2026-07-04T10:00:00Z",
        finished_at="2026-07-04T10:01:00Z",
    )

    assert record.created_at == "2026-07-04T10:00:00Z"
    assert record.finished_at == "2026-07-04T10:01:00Z"


def test_report_record_rejects_invalid_state_combinations():
    with pytest.raises(ValidationError):
        ReportRecord(status="processing")

    with pytest.raises(ValidationError):
        ReportRecord(status="completed")

    with pytest.raises(ValidationError):
        ReportRecord(status="failed")


def test_report_quality_failed_is_a_report_generation_failure():
    error = ReportQualityFailed("summary must include Simplified Chinese text")

    assert isinstance(error, ReportGenerationFailed)
    assert "summary must include Simplified Chinese text" in str(error)
