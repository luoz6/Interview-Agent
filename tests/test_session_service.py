from app.services.prep import InterviewPlan, InterviewQuestion
from app.services.report import DimensionScores, InterviewReport
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

    assert response.follow_up == (
        "请继续深挖 project：你当时做了什么取舍，为什么这样选？"
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
