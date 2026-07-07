from copy import deepcopy

from app.agents.examiner import (
    ExaminerAgent,
    fallback_followup as examiner_fallback_followup,
)
from app.graphs.interview_state import (
    InterviewState,
    build_initial_state,
    count_candidate_answers_for_question,
    get_current_question,
)
from app.services.llm import InterviewLLM
from app.services.prep import InterviewPlan

INTERVIEW_FINISHED_MESSAGE = "本次模拟面试已结束。"


class InterviewGraphRunner:
    def __init__(self, llm: InterviewLLM | None = None, examiner=None) -> None:
        self._llm = llm
        self._examiner = examiner or ExaminerAgent(llm=llm)

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
        next_state = _append_candidate_answer(state, answer)
        next_state = brain_node(next_state, self._llm, examiner=self._examiner)
        return speaker_node(next_state)

    def prepare_answer(self, state: InterviewState, answer: str) -> InterviewState:
        next_state = _append_candidate_answer(state, answer)
        return brain_node(
            next_state,
            self._llm,
            examiner=self._examiner,
            generate_followup_text=False,
        )

    def finalize_prepared_answer(
        self,
        state: InterviewState,
        *,
        follow_up: str | None = None,
    ) -> InterviewState:
        next_state = deepcopy(state)
        if follow_up is not None and next_state["decision"] is not None:
            next_state["decision"]["follow_up"] = follow_up
        return speaker_node(next_state)

    def stream_followup(self, state: InterviewState):
        question = get_current_question(state)
        focus = question.focus if question is not None else "current question"
        yield from self._examiner.stream_followup(
            context=_build_followup_context(state),
            focus=focus,
        )


def brain_node(
    state: InterviewState,
    llm: InterviewLLM | None,
    *,
    examiner=None,
    generate_followup_text: bool = True,
) -> InterviewState:
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

    follow_up = None
    if generate_followup_text:
        resolved_examiner = examiner or ExaminerAgent(llm=llm)
        follow_up = resolved_examiner.generate_followup(
            context=_build_followup_context(state),
            focus=question.focus,
        )

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
        state["pending_output"] = INTERVIEW_FINISHED_MESSAGE
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
            state["pending_output"] = INTERVIEW_FINISHED_MESSAGE
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
    state["pending_output"] = INTERVIEW_FINISHED_MESSAGE
    state["messages"].append(
        {
            "role": "interviewer",
            "content": INTERVIEW_FINISHED_MESSAGE,
            "question_id": None,
        }
    )
    return state


def fallback_followup(focus: str) -> str:
    return examiner_fallback_followup(focus)


def _append_candidate_answer(state: InterviewState, answer: str) -> InterviewState:
    next_state = deepcopy(state)
    question = get_current_question(next_state)
    if question is None:
        next_state["status"] = "finished"
        next_state["decision"] = {
            "action": "finish",
            "follow_up": None,
            "reason": "all_questions_completed",
        }
        next_state["pending_output"] = INTERVIEW_FINISHED_MESSAGE
        return next_state

    next_state["messages"].append(
        {
            "role": "candidate",
            "content": answer.strip(),
            "question_id": question.id,
        }
    )
    return next_state


def _build_followup_context(state: InterviewState) -> list[dict[str, str]]:
    return [
        {"role": message["role"], "content": message["content"]}
        for message in state["messages"][-4:]
    ]
