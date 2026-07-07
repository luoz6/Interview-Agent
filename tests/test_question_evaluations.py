from app.services.prep import InterviewPlan, InterviewQuestion
from app.services.question_evaluations import (
    QuestionEvaluationRecord,
    question_evaluation_from_feedback,
)
from app.services.report import DimensionScores, InterviewFeedback
from app.services.session import InterviewSessionStore


def make_feedback() -> InterviewFeedback:
    return InterviewFeedback(
        question_id="q1",
        question_text="Explain Redis cache invalidation.",
        user_answer="Delete cache after database update.",
        score=82,
        dimension_scores=DimensionScores(
            breadth=80,
            depth=82,
            architecture=81,
            engineering=84,
            communication=83,
        ),
        rationale="The answer covered delete-after-write.",
        critique="It missed race condition handling.",
        better_answer="Mention delayed double delete and retry.",
        references=[],
    )


def make_plan() -> InterviewPlan:
    return InterviewPlan(
        title="Backend plan",
        questions=[
            InterviewQuestion(
                id="q1",
                kind="technical",
                prompt="Explain Redis cache invalidation.",
                focus="Redis consistency",
            )
        ],
    )


def test_question_evaluation_can_be_created_from_feedback():
    record = question_evaluation_from_feedback(
        session_id="s1",
        feedback=make_feedback(),
    )

    assert record.session_id == "s1"
    assert record.question_id == "q1"
    assert record.status == "completed"
    assert record.answer_state == "answered"
    assert record.feedback.score == 82


def test_in_memory_session_store_saves_question_evaluations():
    store = InterviewSessionStore()
    turn = store.start(
        make_plan(),
        job_description="Backend role",
        resume_text="Built APIs",
        job_tags=["backend"],
    )
    record = question_evaluation_from_feedback(
        session_id=turn.session_id,
        feedback=make_feedback(),
    )

    store.save_question_evaluations(turn.session_id, [record])

    saved = store.list_question_evaluations(turn.session_id)
    assert len(saved) == 1
    assert saved[0].question_id == "q1"


def test_question_evaluation_record_requires_error_for_failed_status():
    try:
        QuestionEvaluationRecord(
            session_id="s1",
            question_id="q1",
            status="failed",
        )
    except ValueError as exc:
        assert "failed question evaluations require error" in str(exc)
    else:
        raise AssertionError("expected validation failure")
