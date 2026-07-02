from typing import Literal, TypedDict

from app.services.prep import InterviewPlan, InterviewQuestion


class InterviewMessage(TypedDict):
    role: Literal["interviewer", "candidate"]
    content: str
    question_id: str | None


class InterviewDecision(TypedDict, total=False):
    action: Literal["follow_up", "next_question", "finish"]
    follow_up: str | None
    reason: str | None


class InterviewState(TypedDict):
    session_id: str
    plan: InterviewPlan
    current_index: int
    messages: list[InterviewMessage]
    decision: InterviewDecision | None
    pending_output: str | None
    status: Literal["active", "finished"]
    job_description: str
    resume_text: str
    job_tags: list[str]


def build_initial_state(
    session_id: str,
    plan: InterviewPlan,
    job_description: str,
    resume_text: str,
    job_tags: list[str],
) -> InterviewState:
    first_question = plan.questions[0] if plan.questions else None
    first_output = (
        first_question.prompt
        if first_question
        else "Interview finished because the plan is empty."
    )
    return {
        "session_id": session_id,
        "plan": plan,
        "current_index": 0,
        "messages": [
            {
                "role": "interviewer",
                "content": first_output,
                "question_id": first_question.id if first_question else None,
            }
        ],
        "decision": None,
        "pending_output": first_output,
        "status": "active" if first_question else "finished",
        "job_description": job_description,
        "resume_text": resume_text,
        "job_tags": job_tags,
    }


def get_current_question(state: InterviewState) -> InterviewQuestion | None:
    current_index = state["current_index"]
    questions = state["plan"].questions
    if current_index >= len(questions):
        return None
    return questions[current_index]


def count_candidate_answers_for_question(
    state: InterviewState,
    question_id: str,
) -> int:
    return sum(
        1
        for message in state["messages"]
        if message["role"] == "candidate" and message["question_id"] == question_id
    )
