from app.domain.interview.errors import SessionVersionConflict
from app.domain.interview.models import InterviewTurn
from app.graphs.interview_state import (
    InterviewState,
    get_current_question,
    utc_now_iso,
)


def extract_follow_up(state: InterviewState) -> str | None:
    decision = state["decision"]
    if decision and decision["action"] == "follow_up":
        return state["pending_output"]
    if state["status"] == "finished":
        return state["pending_output"]
    return None


def ensure_expected_version(
    state: InterviewState,
    expected_version: int | None,
) -> None:
    if expected_version is None:
        return
    if expected_version != state["state_version"]:
        raise SessionVersionConflict(
            expected_version=expected_version,
            actual_version=state["state_version"],
        )


def is_duplicate_command(
    state: InterviewState,
    command_id: str | None,
) -> bool:
    return bool(command_id and state.get("last_command_id") == command_id)


def advance_state_metadata(
    state: InterviewState,
    *,
    command_id: str | None,
    record_command_id: bool = True,
) -> InterviewState:
    state["state_version"] += 1
    # Local stores keep checkpoints inline, so the two versions advance
    # together until an external checkpoint adapter owns that boundary.
    state["checkpoint_version"] = state["state_version"]
    state["last_checkpoint_at"] = utc_now_iso()
    if record_command_id:
        state["last_command_id"] = command_id
    return state


def already_finalized_streaming_answer(state: InterviewState) -> bool:
    if not state["messages"]:
        return False
    if state["messages"][-1]["role"] != "interviewer":
        return False
    return state["decision"] is not None


def should_stream_follow_up(state: InterviewState) -> bool:
    decision = state["decision"]
    if decision is None or decision["action"] != "follow_up":
        return False
    return not already_finalized_streaming_answer(state)


def turn_from_state(
    state: InterviewState,
    *,
    follow_up: str | None,
) -> InterviewTurn:
    current_question = (
        None
        if state["status"] == "finished"
        else get_current_question(state)
    )
    return InterviewTurn(
        session_id=state["session_id"],
        current_question=current_question,
        follow_up=follow_up,
        status="finished" if state["status"] == "finished" else "active",
    )


class SessionStateMachine:
    """Named domain boundary over the canonical session state rules."""

    advance_metadata = staticmethod(advance_state_metadata)
    already_finalized_streaming_answer = staticmethod(
        already_finalized_streaming_answer
    )
    ensure_expected_version = staticmethod(ensure_expected_version)
    extract_follow_up = staticmethod(extract_follow_up)
    is_duplicate_command = staticmethod(is_duplicate_command)
    should_stream_follow_up = staticmethod(should_stream_follow_up)
    turn_from_state = staticmethod(turn_from_state)


__all__ = [
    "advance_state_metadata",
    "already_finalized_streaming_answer",
    "ensure_expected_version",
    "extract_follow_up",
    "is_duplicate_command",
    "SessionStateMachine",
    "should_stream_follow_up",
    "turn_from_state",
]
