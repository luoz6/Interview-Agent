from app.agents.examiner import fallback_followup
from app.services.prep import InterviewPlan, InterviewQuestion
from app.services.session import InterviewSessionStore


class NoopLLM:
    pass


def make_store():
    store = InterviewSessionStore(llm=NoopLLM())
    turn = store.start(
        InterviewPlan(
            title="Assistance",
            questions=[
                InterviewQuestion(
                    id="q1",
                    kind="technical",
                    prompt="Explain retries.",
                    focus="reliability",
                )
            ],
        ),
        job_description="Backend",
        resume_text="Services",
        job_tags=["reliability"],
    )
    return store, turn.session_id


def test_artifact_fallback_is_transparent_to_candidate_snapshot():
    store, session_id = make_store()
    store.get(session_id)["context_route"] = "artifact_fallback"

    snapshot = store.snapshot(session_id)

    assert snapshot["context_route"] == "artifact_fallback"
    assert snapshot["assistance_mode"] == "full"
    assert snapshot["user_notice_required"] is False
    assert snapshot["policy_version"] == "deterministic-v1"
    assert "artifact_ref" not in snapshot


def test_template_followup_requires_bounded_basic_mode_notice():
    store, session_id = make_store()
    state = store.get(session_id)
    question = state["plan"].questions[0]
    state["messages"].extend(
        [
            {
                "role": "candidate",
                "content": "I used retries.",
                "question_id": question.id,
            },
            {
                "role": "interviewer",
                "content": fallback_followup(question.focus),
                "question_id": question.id,
            },
        ]
    )

    snapshot = store.snapshot(session_id)

    assert snapshot["assistance_mode"] == "basic"
    assert snapshot["user_notice_required"] is True
    assert snapshot["context_route"] == "deterministic"
