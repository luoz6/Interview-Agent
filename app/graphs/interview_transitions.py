from datetime import datetime, timezone

from app.graphs.interview_graph import INTERVIEW_FINISHED_MESSAGE
from app.graphs.interview_state import (
    InterviewState,
    count_candidate_answers_for_question,
    get_current_question,
    utc_now_iso,
)


def finish_interview_state(state: InterviewState) -> InterviewState:
    if state["status"] == "finished":
        return state

    _ensure_state_metadata(state)
    state["current_index"] = len(state["plan"].questions)
    state["decision"] = {
        "action": "finish",
        "follow_up": None,
        "reason": "user_finished_interview",
    }
    state["pending_output"] = INTERVIEW_FINISHED_MESSAGE
    state["status"] = "finished"
    if not _has_terminal_message(state):
        state["messages"].append(
            {
                "role": "interviewer",
                "content": INTERVIEW_FINISHED_MESSAGE,
                "question_id": None,
            }
        )
    state["finished_at"] = state["finished_at"] or utc_now_iso()
    return state


def skip_interview_question_state(state: InterviewState) -> InterviewState:
    if state["status"] == "finished":
        return state

    _ensure_state_metadata(state)
    _record_skip_if_unanswered(state)
    next_index = state["current_index"] + 1
    if next_index >= len(state["plan"].questions):
        return finish_interview_state(state)

    next_question = state["plan"].questions[next_index]
    state["current_index"] = next_index
    state["decision"] = {
        "action": "next_question",
        "follow_up": None,
        "reason": "user_skipped_question",
    }
    state["pending_output"] = next_question.prompt
    state["messages"].append(
        {
            "role": "interviewer",
            "content": next_question.prompt,
            "question_id": next_question.id,
        }
    )
    return state


def _elapsed_seconds(state: InterviewState) -> int:
    started = _parse_state_timestamp(state.get("started_at"))
    if started is None:
        return 0
    finished = _parse_state_timestamp(state.get("finished_at")) or datetime.now(timezone.utc)
    return max(0, int((finished - started).total_seconds()))


def _question_state(state: InterviewState, index: int) -> str:
    _ensure_state_metadata(state)
    question = state["plan"].questions[index]
    if question.id in state["skipped_question_ids"]:
        return "skipped"
    if count_candidate_answers_for_question(state, question.id) > 0:
        return "answered"
    if state["status"] == "finished":
        return "unanswered"
    if index == state["current_index"]:
        return "current"
    return "pending"


def _question_answer_counts(state: InterviewState) -> dict[str, int]:
    counts = {"answered": 0, "skipped": 0, "unanswered": 0, "pending_or_current": 0}
    for index, _ in enumerate(state["plan"].questions):
        value = _question_state(state, index)
        if value in ("answered", "skipped", "unanswered"):
            counts[value] += 1
        else:
            counts["unanswered"] += 1
            counts["pending_or_current"] += 1
    return counts


def _ensure_state_metadata(state: InterviewState) -> None:
    state.setdefault("phase", "interview")
    state.setdefault("phase_status", "active" if state["status"] == "active" else "completed")
    state.setdefault("review_status", "idle")
    state.setdefault("skipped_question_ids", [])
    state.setdefault("started_at", utc_now_iso())
    state.setdefault("finished_at", None)
    state.setdefault("state_version", 1)
    state.setdefault("checkpoint_version", state["state_version"])
    state.setdefault("last_checkpoint_at", state["started_at"])
    state.setdefault("last_command_id", None)


def _record_skip_if_unanswered(state: InterviewState) -> None:
    question = get_current_question(state)
    if question is None:
        return
    if count_candidate_answers_for_question(state, question.id) > 0:
        return
    if question.id not in state["skipped_question_ids"]:
        state["skipped_question_ids"].append(question.id)


def _parse_state_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _has_terminal_message(state: InterviewState) -> bool:
    return bool(
        state["messages"]
        and state["messages"][-1]["role"] == "interviewer"
        and state["messages"][-1]["content"] == INTERVIEW_FINISHED_MESSAGE
        and state["messages"][-1]["question_id"] is None
    )
