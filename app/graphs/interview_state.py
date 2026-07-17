from datetime import datetime, timezone
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
    phase: Literal["prep", "interview", "review"]
    phase_status: Literal["pending", "active", "completed", "failed"]
    review_status: Literal["idle", "processing", "completed", "failed"]
    job_description: str
    resume_text: str
    job_tags: list[str]
    skipped_question_ids: list[str]
    started_at: str
    finished_at: str | None
    state_version: int
    checkpoint_version: int
    last_checkpoint_at: str | None
    last_command_id: str | None


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


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
    now = utc_now_iso()
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
        "phase": "interview",
        "phase_status": "active" if first_question else "completed",
        "review_status": "idle",
        "job_description": job_description,
        "resume_text": resume_text,
        "job_tags": job_tags,
        "skipped_question_ids": [],
        "started_at": now,
        "finished_at": now if first_question is None else None,
        "state_version": 1,
        "checkpoint_version": 1,
        "last_checkpoint_at": now,
        "last_command_id": None,
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
