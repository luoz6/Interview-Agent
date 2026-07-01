from app.graphs.interview_state import (
    InterviewDecision,
    InterviewMessage,
    build_initial_state,
    get_current_question,
)
from app.graphs.interview_graph import InterviewGraphRunner
from app.services.prep import InterviewPlan, InterviewQuestion


def make_plan():
    return InterviewPlan(
        title="后端模拟面试",
        questions=[
            InterviewQuestion(id="q1", kind="project", prompt="请介绍项目。", focus="项目"),
            InterviewQuestion(id="q2", kind="technical", prompt="请解释 Redis。", focus="Redis"),
            InterviewQuestion(id="q3", kind="system-design", prompt="请设计服务。", focus="系统设计"),
        ],
    )


def test_build_initial_state_records_first_question():
    state = build_initial_state(session_id="s1", plan=make_plan())

    assert state["session_id"] == "s1"
    assert state["current_index"] == 0
    assert state["status"] == "active"
    assert state["decision"] is None
    assert state["pending_output"] == "请介绍项目。"
    assert state["messages"] == [
        {"role": "interviewer", "content": "请介绍项目。", "question_id": "q1"}
    ]


def test_get_current_question_returns_none_after_last_question():
    state = build_initial_state(session_id="s1", plan=make_plan())
    state["current_index"] = 3

    assert get_current_question(state) is None


def test_state_types_accept_decision_and_message_shapes():
    message: InterviewMessage = {
        "role": "candidate",
        "content": "我做过缓存项目。",
        "question_id": "q1",
    }
    decision: InterviewDecision = {
        "action": "follow_up",
        "follow_up": "请继续说明缓存失效策略。",
        "reason": "needs_depth",
    }

    assert message["role"] == "candidate"
    assert decision["action"] == "follow_up"


class FakeLLM:
    def generate_plan(self, job_description: str, resume_text: str):
        raise AssertionError("Graph tests should not generate plans")

    def generate_followup(self, context: list[dict[str, str]]) -> str:
        return "请继续说明缓存失效策略。"


def test_runner_start_returns_initial_state():
    runner = InterviewGraphRunner(llm=FakeLLM())

    state = runner.start(session_id="s1", plan=make_plan())

    assert state["session_id"] == "s1"
    assert state["pending_output"] == "请介绍项目。"
    assert state["messages"][0]["role"] == "interviewer"
    assert state["messages"][0]["question_id"] == "q1"
