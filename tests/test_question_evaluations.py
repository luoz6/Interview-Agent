from app.services.prep import InterviewPlan, InterviewQuestion
from app.services.question_evaluations import (
    QuestionEvaluationRecord,
    question_evaluation_from_feedback,
)
from app.services.report import DimensionScores, InterviewFeedback
from app.services.session import InterviewSessionStore


def make_feedback(
    *,
    question_id: str = "q1",
    score: int = 82,
    answer_state: str = "answered",
) -> InterviewFeedback:
    return InterviewFeedback(
        question_id=question_id,
        question_text=f"Question for {question_id}.",
        user_answer=f"Answer for {question_id}.",
        answer_state=answer_state,
        score=score,
        dimension_scores=DimensionScores(
            breadth=score,
            depth=score,
            architecture=score,
            engineering=score,
            communication=score,
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
            ),
            InterviewQuestion(
                id="q2",
                kind="technical",
                prompt="Explain Redis persistence.",
                focus="Redis durability",
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


def test_question_evaluation_from_feedback_allows_explicit_answer_state_override():
    record = question_evaluation_from_feedback(
        session_id="s1",
        feedback=make_feedback(answer_state="answered"),
        answer_state="skipped",
    )

    assert record.answer_state == "skipped"
    assert record.feedback.answer_state == "answered"


def test_in_memory_session_store_upserts_single_question_evaluation():
    store = InterviewSessionStore()
    turn = store.start(
        make_plan(),
        job_description="Backend role",
        resume_text="Built APIs",
        job_tags=["backend"],
    )
    first = question_evaluation_from_feedback(
        session_id=turn.session_id,
        feedback=make_feedback(score=71),
    )
    replacement = question_evaluation_from_feedback(
        session_id=turn.session_id,
        feedback=make_feedback(score=91),
    )

    store.upsert_question_evaluation(turn.session_id, first)
    initial_created_at = store.list_question_evaluations(turn.session_id)[0].created_at
    store.upsert_question_evaluation(turn.session_id, replacement)

    saved = store.list_question_evaluations(turn.session_id)
    assert len(saved) == 1
    assert saved[0].question_id == "q1"
    assert saved[0].feedback.score == 91
    assert saved[0].created_at == initial_created_at


def test_in_memory_session_store_bulk_save_merges_existing_question_evaluations():
    store = InterviewSessionStore()
    turn = store.start(
        make_plan(),
        job_description="Backend role",
        resume_text="Built APIs",
        job_tags=["backend"],
    )
    q1_initial = question_evaluation_from_feedback(
        session_id=turn.session_id,
        feedback=make_feedback(question_id="q1", score=71),
    )
    q2_initial = question_evaluation_from_feedback(
        session_id=turn.session_id,
        feedback=make_feedback(question_id="q2", score=64),
    )
    q1_final = question_evaluation_from_feedback(
        session_id=turn.session_id,
        feedback=make_feedback(question_id="q1", score=89),
    )

    store.upsert_question_evaluation(turn.session_id, q1_initial)
    store.upsert_question_evaluation(turn.session_id, q2_initial)
    q1_created_at = {
        record.question_id: record.created_at
        for record in store.list_question_evaluations(turn.session_id)
    }["q1"]
    store.save_question_evaluations(turn.session_id, [q1_final])

    saved = {
        record.question_id: record
        for record in store.list_question_evaluations(turn.session_id)
    }
    assert set(saved) == {"q1", "q2"}
    assert saved["q1"].feedback.score == 89
    assert saved["q2"].feedback.score == 64
    assert saved["q1"].created_at == q1_created_at


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
