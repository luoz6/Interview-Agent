from copy import deepcopy

from app.graphs.interview_state import (
    InterviewState,
    build_initial_state,
    get_current_question,
)
from app.services.llm import InterviewLLM
from app.services.prep import InterviewPlan


class InterviewGraphRunner:
    def __init__(self, llm: InterviewLLM | None = None) -> None:
        self._llm = llm

    def start(self, session_id: str, plan: InterviewPlan) -> InterviewState:
        return build_initial_state(session_id=session_id, plan=plan)

    def submit_answer(self, state: InterviewState, answer: str) -> InterviewState:
        next_state = deepcopy(state)
        question = get_current_question(next_state)
        if question is None:
            next_state["status"] = "finished"
            next_state["decision"] = {
                "action": "finish",
                "follow_up": None,
                "reason": "all_questions_completed",
            }
            next_state["pending_output"] = "本次模拟面试已结束。"
            return next_state

        next_state["messages"].append(
            {
                "role": "candidate",
                "content": answer.strip(),
                "question_id": question.id,
            }
        )
        next_state = brain_node(next_state, self._llm)
        return speaker_node(next_state)


def brain_node(state: InterviewState, llm: InterviewLLM | None) -> InterviewState:
    question = get_current_question(state)
    if question is None:
        state["decision"] = {
            "action": "finish",
            "follow_up": None,
            "reason": "all_questions_completed",
        }
        return state

    try:
        if llm is None:
            from app.services.llm import OpenAIInterviewLLM

            llm = OpenAIInterviewLLM()
        follow_up = llm.generate_followup(_build_followup_context(state))
    except Exception:
        follow_up = fallback_followup(question.focus)

    state["decision"] = {
        "action": "follow_up",
        "follow_up": follow_up,
        "reason": "candidate_answer_needs_depth",
    }
    return state


def speaker_node(state: InterviewState) -> InterviewState:
    decision = state["decision"]
    question = get_current_question(state)
    if decision is None or question is None:
        state["status"] = "finished"
        state["pending_output"] = "本次模拟面试已结束。"
        return state

    if decision["action"] == "follow_up":
        output = decision.get("follow_up") or fallback_followup(question.focus)
        state["pending_output"] = output
        state["messages"].append(
            {"role": "interviewer", "content": output, "question_id": question.id}
        )
    return state


def fallback_followup(focus: str) -> str:
    return f"请继续深挖{focus}：你当时做了什么取舍，为什么这样选？"


def _build_followup_context(state: InterviewState) -> list[dict[str, str]]:
    return [
        {"role": message["role"], "content": message["content"]}
        for message in state["messages"][-4:]
    ]
