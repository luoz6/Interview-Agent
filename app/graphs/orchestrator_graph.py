from copy import deepcopy
from typing import Literal, TypedDict

from langgraph.graph import END, START, StateGraph

from app.graphs.interview_graph import InterviewGraphRunner
from app.graphs.interview_state import InterviewState


class OrchestratorCommand(TypedDict, total=False):
    kind: Literal[
        "answer",
        "prepare_stream",
        "complete_stream",
        "skip",
        "finish",
        "sync_review",
    ]
    answer: str
    follow_up_text: str | None
    report_status: Literal["idle", "processing", "completed", "failed"]
    command_id: str | None


class OrchestratorGraphState(TypedDict):
    state: InterviewState
    command: OrchestratorCommand


def build_orchestrator_graph(*, interview_runner: InterviewGraphRunner):
    graph = StateGraph(OrchestratorGraphState)
    graph.add_node(
        "interview_phase",
        lambda payload: _run_interview_phase(payload, interview_runner),
    )
    graph.add_node("review_phase", _run_review_phase)
    graph.add_conditional_edges(
        START,
        _route_command,
        {
            "interview_phase": "interview_phase",
            "review_phase": "review_phase",
        },
    )
    graph.add_edge("interview_phase", END)
    graph.add_edge("review_phase", END)
    return graph.compile()


def _route_command(payload: OrchestratorGraphState) -> str:
    if payload["command"]["kind"] == "sync_review":
        return "review_phase"
    return "interview_phase"


def _run_interview_phase(
    payload: OrchestratorGraphState,
    interview_runner: InterviewGraphRunner,
) -> OrchestratorGraphState:
    state = deepcopy(payload["state"])
    command = payload["command"]
    kind = command["kind"]

    if kind == "answer":
        next_state = interview_runner.submit_answer(
            state,
            command["answer"],
            command_id=command.get("command_id"),
        )
    elif kind == "prepare_stream":
        next_state = interview_runner.prepare_answer(
            state,
            command["answer"],
            command_id=command.get("command_id"),
        )
    elif kind == "complete_stream":
        next_state = interview_runner.finalize_prepared_answer(
            state,
            follow_up=command.get("follow_up_text"),
        )
    elif kind == "skip":
        from app.graphs.interview_transitions import skip_interview_question_state

        next_state = skip_interview_question_state(state)
    elif kind == "finish":
        from app.graphs.interview_transitions import finish_interview_state

        next_state = finish_interview_state(state)
    else:
        raise RuntimeError(f"unsupported orchestrator command: {kind}")

    if next_state["status"] == "finished":
        next_state["phase"] = "review"
        next_state["phase_status"] = "active"
        next_state["review_status"] = "processing"
    return {"state": next_state, "command": command}


def _run_review_phase(payload: OrchestratorGraphState) -> OrchestratorGraphState:
    state = deepcopy(payload["state"])
    report_status = payload["command"]["report_status"]
    state["phase"] = "review"
    state["review_status"] = report_status
    if report_status == "completed":
        state["phase_status"] = "completed"
    elif report_status == "failed":
        state["phase_status"] = "failed"
    else:
        state["phase_status"] = "active"
    return {"state": state, "command": payload["command"]}
