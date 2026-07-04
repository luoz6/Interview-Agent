from app.graphs.interview_graph import INTERVIEW_FINISHED_MESSAGE
from app.services.prep import InterviewPlan, InterviewQuestion
from app.services.report import InterviewReport
from app.services.session import InterviewSessionStore


class FakeInterviewLLM:
    def __init__(self):
        self.last_context = None
        self.should_fail_followup = False

    def generate_plan(self, job_description: str, resume_text: str):
        return InterviewPlan(
            title="LLM generated backend interview",
            questions=[
                InterviewQuestion(
                    id="q1",
                    kind="project",
                    prompt="Introduce one project.",
                    focus="project",
                ),
                InterviewQuestion(
                    id="q2",
                    kind="technical",
                    prompt="Explain Redis.",
                    focus="Redis",
                ),
                InterviewQuestion(
                    id="q3",
                    kind="system-design",
                    prompt="Design a backend service.",
                    focus="system design",
                ),
            ],
        )

    def generate_followup(self, context: list[dict[str, str]]) -> str:
        self.last_context = context
        if self.should_fail_followup:
            raise RuntimeError("llm failed")
        return (
            "You mentioned caching. Please explain how you protect the database "
            "when the cache becomes invalid."
        )

    def stream_followup(self, context: list[dict[str, str]]):
        self.last_context = context
        if self.should_fail_followup:
            raise RuntimeError("llm failed")
        yield "You mentioned caching. "
        yield "Please explain how you protect the database "
        yield "when the cache becomes invalid."

    def generate_report(
        self,
        plan: InterviewPlan,
        evaluation_items: list[dict],
        session_id: str,
    ) -> InterviewReport:
        raise AssertionError("Session store tests do not generate reports")


def make_plan():
    return FakeInterviewLLM().generate_plan("Backend role", "Backend resume")


def start_session(store: InterviewSessionStore):
    return store.start(
        make_plan(),
        job_description="Backend role using Python and Redis.",
        resume_text="Built a Python API with Redis.",
        job_tags=["python", "redis"],
    )


def test_start_session_returns_first_question():
    store = InterviewSessionStore(llm=FakeInterviewLLM())

    session = start_session(store)

    assert session.session_id
    assert session.current_question is not None
    assert session.current_question.kind == "project"
    assert session.status == "active"


def test_start_session_records_job_context_in_store():
    store = InterviewSessionStore(llm=FakeInterviewLLM())

    session = start_session(store)

    state = store.get(session.session_id)
    assert state["job_tags"] == ["python", "redis"]
    assert state["job_description"] == "Backend role using Python and Redis."
    assert state["resume_text"] == "Built a Python API with Redis."


def test_submit_answer_uses_llm_context_to_generate_followup():
    llm = FakeInterviewLLM()
    store = InterviewSessionStore(llm=llm)
    session = start_session(store)

    response = store.submit_answer(
        session.session_id,
        "I used Redis to cache frequently requested records.",
    )

    assert response.follow_up == (
        "You mentioned caching. Please explain how you protect the database "
        "when the cache becomes invalid."
    )
    assert llm.last_context == [
        {"role": "interviewer", "content": "Introduce one project."},
        {
            "role": "candidate",
            "content": "I used Redis to cache frequently requested records.",
        },
    ]


def test_submit_answer_falls_back_when_llm_followup_fails():
    llm = FakeInterviewLLM()
    llm.should_fail_followup = True
    store = InterviewSessionStore(llm=llm)
    session = start_session(store)

    response = store.submit_answer(
        session.session_id,
        "I used Redis to cache frequently requested records.",
    )

    assert response.follow_up == "请继续深挖 project：你当时做了什么取舍，为什么这样选？"


def test_prepare_and_complete_streaming_answer_persists_followup():
    llm = FakeInterviewLLM()
    store = InterviewSessionStore(llm=llm)
    session = start_session(store)

    prepared = store.prepare_streaming_answer(
        session.session_id,
        "I used Redis to cache frequently requested records.",
    )
    assert prepared.stream_follow_up is True

    chunks = list(store.stream_followup(session.session_id))
    finalized = store.complete_streaming_answer(
        session.session_id,
        follow_up_text="".join(chunks),
    )

    assert chunks == [
        "You mentioned caching. ",
        "Please explain how you protect the database ",
        "when the cache becomes invalid.",
    ]
    assert finalized["pending_output"] == (
        "You mentioned caching. Please explain how you protect the database "
        "when the cache becomes invalid."
    )


def test_submit_answer_advances_after_followup():
    store = InterviewSessionStore(llm=FakeInterviewLLM())
    session = start_session(store)

    store.submit_answer(
        session.session_id,
        "I used Redis to cache frequently requested records.",
    )
    second_response = store.submit_answer(
        session.session_id,
        "I used logical expiration and rate limiting.",
    )

    assert second_response.current_question is not None
    assert second_response.current_question.id == "q2"
    state = store.get(session.session_id)
    assert (
        len([message for message in state["messages"] if message["role"] == "candidate"])
        == 2
    )


def test_store_persists_graph_messages_in_order():
    store = InterviewSessionStore(llm=FakeInterviewLLM())
    session = start_session(store)

    store.submit_answer(
        session.session_id,
        "I used Redis to cache frequently requested records.",
    )
    store.submit_answer(
        session.session_id,
        "I used logical expiration and rate limiting.",
    )
    state = store.get(session.session_id)

    assert [message["role"] for message in state["messages"]] == [
        "interviewer",
        "candidate",
        "interviewer",
        "candidate",
        "interviewer",
    ]
    assert state["messages"][-1]["content"] == "Explain Redis."


def test_session_snapshot_includes_progress_tags_questions_and_messages():
    store = InterviewSessionStore(llm=FakeInterviewLLM())
    session = start_session(store)

    snapshot = store.snapshot(session.session_id)

    assert snapshot["session_id"] == session.session_id
    assert snapshot["status"] == "active"
    assert snapshot["current_index"] == 0
    assert snapshot["total_questions"] == 3
    assert snapshot["completed_questions"] == 0
    assert snapshot["current_question"]["id"] == "q1"
    assert snapshot["questions"][0]["state"] == "current"
    assert snapshot["questions"][1]["state"] == "pending"
    assert snapshot["messages"][0]["role"] == "interviewer"
    assert snapshot["job_tags"] == ["python", "redis"]


def test_session_snapshot_marks_completed_question_after_advance():
    store = InterviewSessionStore(llm=FakeInterviewLLM())
    session = start_session(store)

    store.submit_answer(session.session_id, "I used Redis cache-aside.")
    store.submit_answer(session.session_id, "I handled misses with fallback.")

    snapshot = store.snapshot(session.session_id)

    assert snapshot["current_question"]["id"] == "q2"
    assert snapshot["completed_questions"] == 1
    assert snapshot["questions"][0]["state"] == "completed"
    assert snapshot["questions"][1]["state"] == "current"


def test_session_snapshot_marks_finished_session_without_current_question():
    store = InterviewSessionStore(llm=FakeInterviewLLM())
    session = start_session(store)

    store.finish(session.session_id)

    snapshot = store.snapshot(session.session_id)

    assert snapshot["status"] == "finished"
    assert snapshot["current_question"] is None
    assert snapshot["completed_questions"] == 3
    assert [question["state"] for question in snapshot["questions"]] == [
        "completed",
        "completed",
        "completed",
    ]


def test_skip_advances_to_next_question():
    store = InterviewSessionStore(llm=FakeInterviewLLM())
    session = start_session(store)

    skipped = store.skip(session.session_id)

    assert skipped.status == "active"
    assert skipped.current_question is not None
    assert skipped.current_question.id == "q2"
    assert skipped.follow_up is None
    snapshot = store.snapshot(session.session_id)
    assert snapshot["questions"][0]["state"] == "completed"
    assert snapshot["questions"][1]["state"] == "current"
    assert snapshot["messages"][-1]["content"] == "Explain Redis."


def test_skip_last_question_finishes_session():
    store = InterviewSessionStore(llm=FakeInterviewLLM())
    session = start_session(store)

    store.skip(session.session_id)
    store.skip(session.session_id)
    final_turn = store.skip(session.session_id)

    assert final_turn.status == "finished"
    assert final_turn.current_question is None
    assert final_turn.follow_up == INTERVIEW_FINISHED_MESSAGE
    snapshot = store.snapshot(session.session_id)
    assert snapshot["status"] == "finished"
    assert snapshot["current_question"] is None
    assert snapshot["messages"][-1]["content"] == INTERVIEW_FINISHED_MESSAGE


def test_skip_finished_session_is_idempotent():
    store = InterviewSessionStore(llm=FakeInterviewLLM())
    session = start_session(store)

    store.finish(session.session_id)
    message_count = len(store.snapshot(session.session_id)["messages"])
    skipped = store.skip(session.session_id)

    assert skipped.status == "finished"
    assert skipped.current_question is None
    assert skipped.follow_up == INTERVIEW_FINISHED_MESSAGE
    snapshot = store.snapshot(session.session_id)
    assert snapshot["status"] == "finished"
    assert len(snapshot["messages"]) == message_count
