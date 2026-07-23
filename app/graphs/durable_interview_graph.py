from __future__ import annotations

from dataclasses import dataclass
from functools import partial
from typing import Any, Callable

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.graphs.durable_interview_state import DurableInterviewState


@dataclass
class DurableInterviewGraphDependencies:
    workflow_store: Any
    project_state: Callable[[DurableInterviewState], dict]


def initialize_session(state: DurableInterviewState) -> dict:
    return {}


def wait_for_answer(state: DurableInterviewState) -> dict:
    payload = interrupt(
        {
            "kind": "answer_command",
            "session_id": state["session_id"],
            "state_version": state["state_version"],
        }
    )
    return {"active_command_id": payload["command_id"]}


def validate_command(state, deps) -> dict:
    command = deps.workflow_store.get_command(
        state["session_id"], state["active_command_id"]
    )
    if command.status == "applied":
        return {"active_command_id": None, "command_outcome": "duplicate"}
    if command.expected_version != state["state_version"]:
        deps.workflow_store.mark_command_conflict(
            state["session_id"],
            command.command_id,
            state["state_version"],
        )
        return {"active_command_id": None, "command_outcome": "conflict"}
    return {
        "command_type": command.command_type,
        "command_outcome": "accepted",
    }


def append_candidate_answer(state, deps) -> dict:
    command = deps.workflow_store.get_command(
        state["session_id"], state["active_command_id"]
    )
    questions = state["plan_snapshot"]["questions"]
    question = questions[state["current_index"]]
    return {
        "messages": [
            *state["messages"],
            {
                "role": "candidate",
                "content": command.answer_text.strip(),
                "question_id": question["id"],
            },
        ]
    }


def apply_skip(state) -> dict:
    questions = state["plan_snapshot"]["questions"]
    question = questions[state["current_index"]]
    next_index = state["current_index"] + 1
    if next_index >= len(questions):
        return {
            "skipped_question_ids": [
                *state["skipped_question_ids"],
                question["id"],
            ],
            "interview_status": "finished",
            "current_index": len(questions),
            "command_outcome": "completed",
        }
    next_question = questions[next_index]
    return {
        "skipped_question_ids": [
            *state["skipped_question_ids"],
            question["id"],
        ],
        "current_index": next_index,
        "messages": [
            *state["messages"],
            {
                "role": "interviewer",
                "content": next_question["prompt"],
                "question_id": next_question["id"],
            },
        ],
        "command_outcome": "completed",
    }


def apply_finish(state) -> dict:
    return {
        "interview_status": "finished",
        "current_index": len(state["plan_snapshot"]["questions"]),
        "command_outcome": "completed",
    }


def decide_next_action(state) -> dict:
    return {"command_outcome": "completed"}


def route_validated_command(state) -> str:
    if state["command_outcome"] in {"duplicate", "conflict"}:
        return "wait_for_answer"
    if state["command_type"] == "answer":
        return "append_candidate_answer"
    if state["command_type"] == "skip":
        return "apply_skip"
    return "apply_finish"


def route_after_projection(state) -> str:
    if state["interview_status"] == "finished":
        return END
    return "wait_for_answer"


def build_durable_interview_graph(
    deps: DurableInterviewGraphDependencies,
    *,
    checkpointer,
):
    builder = StateGraph(DurableInterviewState)
    builder.add_node("initialize_session", initialize_session)
    builder.add_node("project_state", deps.project_state)
    builder.add_node("wait_for_answer", wait_for_answer)
    builder.add_node(
        "validate_command", partial(validate_command, deps=deps)
    )
    builder.add_node(
        "append_candidate_answer",
        partial(append_candidate_answer, deps=deps),
    )
    builder.add_node("apply_skip", apply_skip)
    builder.add_node("apply_finish", apply_finish)
    builder.add_node("decide_next_action", decide_next_action)
    builder.add_edge(START, "initialize_session")
    builder.add_edge("initialize_session", "project_state")
    builder.add_conditional_edges("project_state", route_after_projection)
    builder.add_edge("wait_for_answer", "validate_command")
    builder.add_conditional_edges(
        "validate_command", route_validated_command
    )
    builder.add_edge("append_candidate_answer", "decide_next_action")
    builder.add_edge("decide_next_action", "project_state")
    builder.add_edge("apply_skip", "project_state")
    builder.add_edge("apply_finish", "project_state")
    return builder.compile(checkpointer=checkpointer)
