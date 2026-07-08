from app.graphs.interview_state import InterviewState
from app.services.prep import InterviewPlan


def build_single_question_review_state(
    state: InterviewState,
    question_id: str,
) -> InterviewState:
    question = next(
        (question for question in state["plan"].questions if question.id == question_id),
        None,
    )
    if question is None:
        raise ValueError(f"question not found: {question_id}")

    prompt_message = {
        "role": "interviewer",
        "content": question.prompt,
        "question_id": question_id,
    }
    messages = [
        dict(message)
        for message in state["messages"]
        if message["question_id"] == question_id
    ]
    if not messages or messages[0] != prompt_message:
        messages = [prompt_message] + [
            message for message in messages if message != prompt_message
        ]

    return {
        "session_id": state["session_id"],
        "plan": InterviewPlan(title=state["plan"].title, questions=[question]),
        "current_index": 1,
        "messages": messages,
        "decision": {
            "action": "finish",
            "follow_up": None,
            "reason": "round_closed",
        },
        "pending_output": None,
        "status": "finished",
        "job_description": state["job_description"],
        "resume_text": state["resume_text"],
        "job_tags": list(state["job_tags"]),
        "skipped_question_ids": [
            skipped_id
            for skipped_id in state.get("skipped_question_ids", [])
            if skipped_id == question_id
        ],
        "started_at": state["started_at"],
        "finished_at": state.get("finished_at") or state["started_at"],
    }
