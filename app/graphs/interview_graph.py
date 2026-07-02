from copy import deepcopy

from app.graphs.interview_state import (
    InterviewState,
    build_initial_state,
    count_candidate_answers_for_question,
    get_current_question,
)
from app.services.llm import InterviewLLM
from app.services.prep import InterviewPlan


class InterviewGraphRunner:
    def __init__(self, llm: InterviewLLM | None = None) -> None:
        self._llm = llm

    def start(
        self,
        session_id: str,
        plan: InterviewPlan,
        job_description: str,
        resume_text: str,
        job_tags: list[str],
    ) -> InterviewState:
        return build_initial_state(
            session_id=session_id,
            plan=plan,
            job_description=job_description,
            resume_text=resume_text,
            job_tags=job_tags,
        )

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

    answer_count = count_candidate_answers_for_question(state, question.id)
    if answer_count >= 2:
        next_index = state["current_index"] + 1
        if next_index >= len(state["plan"].questions):
            state["decision"] = {
                "action": "finish",
                "follow_up": None,
                "reason": "all_questions_completed",
            }
        else:
            state["decision"] = {
                "action": "next_question",
                "follow_up": None,
                "reason": "question_completed",
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
    if decision is None:
        state["status"] = "finished"
        state["pending_output"] = "本次模拟面试已结束。"
        return state

    action = decision["action"]
    question = get_current_question(state)

    if action == "follow_up" and question is not None:
        output = decision.get("follow_up") or fallback_followup(question.focus)
        state["pending_output"] = output
        state["messages"].append(
            {"role": "interviewer", "content": output, "question_id": question.id}
        )
        return state

    if action == "next_question":
        state["current_index"] += 1
        next_question = get_current_question(state)
        if next_question is None:
            state["status"] = "finished"
            state["pending_output"] = "本次模拟面试已结束。"
            return state
        state["pending_output"] = next_question.prompt
        state["messages"].append(
            {
                "role": "interviewer",
                "content": next_question.prompt,
                "question_id": next_question.id,
            }
        )
        return state

    state["current_index"] = len(state["plan"].questions)
    state["status"] = "finished"
    state["pending_output"] = "本次模拟面试已结束。"
    state["messages"].append(
        {
            "role": "interviewer",
            "content": "本次模拟面试已结束。",
            "question_id": None,
        }
    )
    return state


def fallback_followup(focus: str) -> str:
    return f"请继续深挖 {focus}：你当时做了什么取舍，为什么这样选？"


def _build_followup_context(state: InterviewState) -> list[dict[str, str]]:
    return [
        {"role": message["role"], "content": message["content"]}
        for message in state["messages"][-4:]
    ]
