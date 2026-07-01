from app.services.prep import prepare_interview
from app.services.session import InterviewSessionStore


def test_start_session_returns_first_question():
    plan = prepare_interview(
        job_description="Python backend role with Redis.",
        resume_text="Built APIs with Python and Redis.",
    )
    store = InterviewSessionStore()

    session = store.start(plan)

    assert session.session_id
    assert session.current_question is not None
    assert session.current_question.kind == "project"
    assert session.status == "active"


def test_submit_answer_records_answer_and_advances_after_followup():
    plan = prepare_interview(
        job_description="Python backend role with Redis.",
        resume_text="Built APIs with Python and Redis.",
    )
    store = InterviewSessionStore()
    session = store.start(plan)

    first_response = store.submit_answer(session.session_id, "I used Redis to cache hot ticket data.")
    second_response = store.submit_answer(session.session_id, "I handled expiration and fallback.")

    assert first_response.follow_up is not None
    assert second_response.current_question is not None
    assert second_response.current_question.id != session.current_question.id
    assert len(store.get(session.session_id).answers) == 2
