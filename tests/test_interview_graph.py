from app.graphs.interview_graph import InterviewGraphRunner
from app.graphs.interview_state import (
    InterviewDecision,
    InterviewMessage,
    build_initial_state,
    get_current_question,
)
from app.services.prep import InterviewPlan, InterviewQuestion
from app.services.report import InterviewReport


def make_plan():
    return InterviewPlan(
        title="Backend mock interview",
        questions=[
            InterviewQuestion(
                id="q1",
                kind="project",
                prompt="Introduce the project.",
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
                prompt="Design the service.",
                focus="system design",
            ),
        ],
    )


def make_start_kwargs():
    return {
        "session_id": "s1",
        "plan": make_plan(),
        "job_description": "Backend role using Python and Redis.",
        "resume_text": "Built a Python API with Redis.",
        "job_tags": ["python", "redis"],
    }


def test_build_initial_state_records_first_question():
    state = build_initial_state(**make_start_kwargs())

    assert state["session_id"] == "s1"
    assert state["current_index"] == 0
    assert state["status"] == "active"
    assert state["decision"] is None
    assert state["pending_output"] == "Introduce the project."
    assert state["messages"] == [
        {"role": "interviewer", "content": "Introduce the project.", "question_id": "q1"}
    ]


def test_get_current_question_returns_none_after_last_question():
    state = build_initial_state(**make_start_kwargs())
    state["current_index"] = 3

    assert get_current_question(state) is None


def test_build_initial_state_records_job_context():
    state = build_initial_state(**make_start_kwargs())

    assert state["job_description"] == "Backend role using Python and Redis."
    assert state["resume_text"] == "Built a Python API with Redis."
    assert state["job_tags"] == ["python", "redis"]


def test_state_types_accept_decision_and_message_shapes():
    message: InterviewMessage = {
        "role": "candidate",
        "content": "I worked on a cache project.",
        "question_id": "q1",
    }
    decision: InterviewDecision = {
        "action": "follow_up",
        "follow_up": "Please explain the cache invalidation strategy.",
        "reason": "needs_depth",
    }

    assert message["role"] == "candidate"
    assert decision["action"] == "follow_up"


class FakeLLM:
    def generate_plan(self, job_description: str, resume_text: str):
        raise AssertionError("Graph tests should not generate plans")

    def generate_followup(self, context: list[dict[str, str]]) -> str:
        return "Please explain the cache invalidation strategy."

    def generate_report(
        self,
        plan: InterviewPlan,
        evaluation_items: list[dict],
        session_id: str,
    ) -> InterviewReport:
        raise AssertionError("Graph tests do not generate reports")


def test_runner_start_returns_initial_state():
    runner = InterviewGraphRunner(llm=FakeLLM())

    state = runner.start(**make_start_kwargs())

    assert state["session_id"] == "s1"
    assert state["pending_output"] == "Introduce the project."
    assert state["messages"][0]["role"] == "interviewer"
    assert state["messages"][0]["question_id"] == "q1"


class FailingLLM:
    def generate_plan(self, job_description: str, resume_text: str):
        raise AssertionError("Graph tests should not generate plans")

    def generate_followup(self, context: list[dict[str, str]]) -> str:
        raise RuntimeError("llm failed")

    def generate_report(
        self,
        plan: InterviewPlan,
        evaluation_items: list[dict],
        session_id: str,
    ) -> InterviewReport:
        raise AssertionError("Graph tests do not generate reports")


def test_runner_submit_answer_generates_followup_decision():
    runner = InterviewGraphRunner(llm=FakeLLM())
    state = runner.start(**make_start_kwargs())

    new_state = runner.submit_answer(state, "I used Redis to cache hot records.")

    assert new_state["decision"] == {
        "action": "follow_up",
        "follow_up": "Please explain the cache invalidation strategy.",
        "reason": "candidate_answer_needs_depth",
    }
    assert new_state["pending_output"] == "Please explain the cache invalidation strategy."
    assert new_state["messages"][-2] == {
        "role": "candidate",
        "content": "I used Redis to cache hot records.",
        "question_id": "q1",
    }
    assert new_state["messages"][-1] == {
        "role": "interviewer",
        "content": "Please explain the cache invalidation strategy.",
        "question_id": "q1",
    }


def test_runner_submit_answer_falls_back_when_llm_fails():
    runner = InterviewGraphRunner(llm=FailingLLM())
    state = runner.start(**make_start_kwargs())

    new_state = runner.submit_answer(state, "I used Redis to cache hot records.")

    assert new_state["decision"]["action"] == "follow_up"
    assert new_state["pending_output"] == "请继续深挖 project：你当时做了什么取舍，为什么这样选？"


def test_runner_advances_to_next_question_after_followup_answer():
    runner = InterviewGraphRunner(llm=FakeLLM())
    state = runner.start(**make_start_kwargs())

    state = runner.submit_answer(state, "I used Redis to cache hot records.")
    state = runner.submit_answer(state, "I used logical expiration and rate limiting.")

    assert state["current_index"] == 1
    assert state["decision"]["action"] == "next_question"
    assert state["pending_output"] == "Explain Redis."
    assert state["messages"][-1] == {
        "role": "interviewer",
        "content": "Explain Redis.",
        "question_id": "q2",
    }


def test_runner_finishes_after_last_question_followup_answer():
    runner = InterviewGraphRunner(llm=FakeLLM())
    state = runner.start(**make_start_kwargs())

    for answer in [
        "Project answer.",
        "Project follow-up answer.",
        "Technical answer.",
        "Technical follow-up answer.",
        "Design answer.",
        "Design follow-up answer.",
    ]:
        state = runner.submit_answer(state, answer)

    assert state["status"] == "finished"
    assert state["current_index"] == 3
    assert state["decision"]["action"] == "finish"
    assert state["pending_output"] == "本次模拟面试已结束。"
