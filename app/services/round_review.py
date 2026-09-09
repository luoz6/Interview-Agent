from app.graphs.interview_state import InterviewState
from app.services.published_question import (
    published_question_text,
    question_id as published_question_id,
)


def build_single_question_review_state(
    state: InterviewState,
    question_id: str,
) -> InterviewState:
    question = next(
        (
            question
            for question in state["plan"].questions
            if published_question_id(question) == question_id
        ),
        None,
    )
    if question is None:
        raise ValueError(f"question not found: {question_id}")

    prompt_message = {
        "role": "interviewer",
        "content": published_question_text(state, question),
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

    review_state = {
        "session_id": state["session_id"],
        "plan": state["plan"].model_copy(update={"questions": [question]}),
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
        "started_at": state.get("started_at"),
        "finished_at": state.get("finished_at") or state.get("started_at"),
    }
    if getattr(state["plan"], "schema_version", None) == "interview-plan-v3":
        review_state.update(
            {
                "workflow_engine": "langgraph-v3",
                "graph_schema_version": state.get(
                    "graph_schema_version", "langgraph-v3"
                ),
                "rendered_questions": dict(
                    state.get("rendered_questions") or {}
                ),
            }
        )
    return review_state
