from __future__ import annotations

from dataclasses import dataclass
from functools import partial
from typing import Any, Callable

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.agents.examiner import fallback_followup
from app.graphs.durable_interview_state import DurableInterviewState
from app.services.agent_runtime import AgentExecutionContext
from app.services.interview_generation_store import ChunkCoalescer
from app.services.interview_generation_store import GenerationAlreadyCompleted
from app.services.knowledge_binding import resolve_evidence_by_ids
from app.services.runtime_work import (
    classify_runtime_failure,
    retry_delay_seconds,
)


@dataclass
class DurableInterviewGraphDependencies:
    workflow_store: Any
    project_state: Callable[[DurableInterviewState], dict] | None = None
    generation_store: Any | None = None
    examiner: Any | None = None
    knowledge_repository: Any | None = None
    report_job_queue: Any | None = None
    context_builder: Callable[[DurableInterviewState], list[dict[str, str]]] | None = None
    coalescer_factory: Callable[[], ChunkCoalescer] = ChunkCoalescer
    worker_id: str = "durable-interview-worker"
    generation_lease_seconds: int = 60
    retryable_provider_errors: tuple[type[Exception], ...] = (
        TimeoutError,
        ConnectionError,
    )
    terminal_provider_errors: tuple[type[Exception], ...] = (
        PermissionError,
    )


def initialize_session(state: DurableInterviewState) -> dict:
    return {}


def project_state_node(state, deps) -> dict:
    if deps.project_state is not None:
        return deps.project_state(state)
    projection = deps.workflow_store.project_state(state)
    updates = {
        "state_version": projection.state_version,
        "command_outcome": None,
        "generation_outcome": None,
        "generated_text": None,
        "retry_resume_attempt": None,
        "retry_validation": None,
    }
    if state["command_outcome"] == "completed":
        updates["active_command_id"] = None
        updates["command_type"] = None
    return updates


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
    return {}


def prepare_generation(state, deps) -> dict:
    question = _current_question(state)
    generation = deps.generation_store.prepare_generation(
        session_id=state["session_id"],
        source_command_id=state["active_command_id"],
        question_id=question["id"],
    )
    return {
        "generation_id": generation.generation_id,
        "generation_attempt": generation.active_attempt,
    }


def generate_followup(state, deps) -> dict:
    coalescer = deps.coalescer_factory()
    try:
        attempt = deps.generation_store.start_or_reclaim_attempt(
            state["generation_id"],
            state["generation_attempt"],
            worker_id=deps.worker_id,
            lease_seconds=deps.generation_lease_seconds,
        )
    except GenerationAlreadyCompleted:
        generation = deps.generation_store.get_by_id(state["generation_id"])
        return {
            "generation_outcome": "completed",
            "generated_text": generation.final_text or "",
        }
    chunks: list[str] = []
    sequence = 0
    context = (
        deps.context_builder(state)
        if deps.context_builder is not None
        else _build_examiner_context(state, deps.knowledge_repository)
    )
    try:
        for chunk in deps.examiner.stream_followup_attempt(
            context=context,
            execution_context=AgentExecutionContext(
                correlation_id=state["session_id"],
                causation_id=state["active_command_id"],
                agent="examiner",
                operation="generate_followup",
                phase="interview",
                session_id=state["session_id"],
                question_id=_current_question(state)["id"],
                state_version=state["state_version"],
                command_id=state["active_command_id"],
                attempt_number=attempt.attempt_number,
            ),
        ):
            chunks.append(chunk)
            persisted = coalescer.add(chunk)
            if persisted:
                sequence += 1
                deps.generation_store.append_chunk(
                    attempt.generation_id,
                    attempt.attempt_number,
                    sequence,
                    persisted,
                )
                deps.generation_store.heartbeat_attempt(
                    attempt.generation_id,
                    attempt.attempt_number,
                    deps.worker_id,
                    lease_seconds=deps.generation_lease_seconds,
                )
        final_chunk = coalescer.flush()
        if final_chunk:
            sequence += 1
            deps.generation_store.append_chunk(
                attempt.generation_id,
                attempt.attempt_number,
                sequence,
                final_chunk,
            )
        final_text = "".join(chunks).strip()
        deps.generation_store.complete_attempt(
            attempt.generation_id,
            attempt.attempt_number,
            final_text,
        )
        return {
            "generation_outcome": "completed",
            "generated_text": final_text,
        }
    except deps.retryable_provider_errors as exc:
        code = classify_runtime_failure(exc).code
        deps.generation_store.fail_attempt(
            attempt.generation_id, attempt.attempt_number, code
        )
        return {
            "generation_outcome": "retryable",
            "last_error_code": code,
        }
    except deps.terminal_provider_errors as exc:
        code = classify_runtime_failure(exc).code
        deps.generation_store.fail_attempt(
            attempt.generation_id, attempt.attempt_number, code
        )
        return {
            "generation_outcome": "terminal",
            "last_error_code": code,
        }


def enqueue_retry(state, deps) -> dict:
    next_attempt = state["generation_attempt"] + 1
    scheduled = deps.workflow_store.enqueue_retry(
        session_id=state["session_id"],
        generation_id=state["generation_id"],
        next_attempt_number=next_attempt,
        delay_seconds=retry_delay_seconds(state["generation_attempt"]),
    )
    return {
        "expected_retry_attempt": next_attempt,
        "next_retry_at": scheduled.available_at.isoformat(),
    }


def wait_for_retry(state) -> dict:
    payload = interrupt(
        {
            "kind": "retry_timer",
            "generation_id": state["generation_id"],
            "next_attempt_number": state["generation_attempt"] + 1,
        }
    )
    return {"retry_resume_attempt": payload["next_attempt_number"]}


def validate_retry(state) -> dict:
    if state["retry_resume_attempt"] != state["expected_retry_attempt"]:
        return {
            "retry_resume_attempt": None,
            "retry_validation": "stale",
        }
    return {
        "generation_attempt": state["expected_retry_attempt"],
        "expected_retry_attempt": None,
        "retry_resume_attempt": None,
        "retry_validation": "accepted",
        "next_retry_at": None,
    }


def prepare_retry(state) -> dict:
    return {"retry_validation": None, "generation_outcome": None}


def commit_interviewer_message(state) -> dict:
    question = _current_question(state)
    return {
        "messages": [
            *state["messages"],
            {
                "role": "interviewer",
                "content": state["generated_text"],
                "question_id": question["id"],
            },
        ],
        "generation_id": None,
        "expected_retry_attempt": None,
        "next_retry_at": None,
        "command_outcome": "completed",
    }


def fallback_followup_node(state) -> dict:
    question = _current_question(state)
    return {"generated_text": fallback_followup(question["focus"])}


def commit_next_question(state) -> dict:
    questions = state["plan_snapshot"]["questions"]
    next_index = state["current_index"] + 1
    if next_index >= len(questions):
        return {
            "interview_status": "finished",
            "current_index": len(questions),
            "command_outcome": "completed",
        }
    question = questions[next_index]
    return {
        "current_index": next_index,
        "messages": [
            *state["messages"],
            {
                "role": "interviewer",
                "content": question["prompt"],
                "question_id": question["id"],
            },
        ],
        "command_outcome": "completed",
    }


def emit_report_event(state, deps) -> dict:
    if deps.report_job_queue is not None:
        deps.report_job_queue.enqueue_report_request(state["session_id"])
    return {}


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
        return "emit_report_event"
    if state["generation_id"] is not None:
        return "generate_followup"
    return "wait_for_answer"


def route_decision(state) -> str:
    question_id = _current_question(state)["id"]
    answers = sum(
        1
        for message in state["messages"]
        if message["role"] == "candidate"
        and message["question_id"] == question_id
    )
    if answers < 2:
        return "prepare_generation"
    return "commit_next_question"


def route_generation(state) -> str:
    if state["generation_outcome"] == "completed":
        return "commit_interviewer_message"
    if (
        state["generation_outcome"] == "retryable"
        and state["generation_attempt"] < 3
    ):
        return "enqueue_retry"
    return "fallback_followup"


def route_validated_retry(state) -> str:
    if state["retry_validation"] == "accepted":
        return "prepare_retry"
    return "wait_for_retry"


def _current_question(state) -> dict:
    return state["plan_snapshot"]["questions"][state["current_index"]]


def _recent_conversation_messages(state) -> list[dict[str, str]]:
    return [
        {"role": message["role"], "content": message["content"]}
        for message in state["messages"][-4:]
    ]


def _build_examiner_context(
    state, repository
) -> list[dict[str, str]]:
    recent = _recent_conversation_messages(state)
    if repository is None:
        return recent
    question = _current_question(state)
    resolution = resolve_evidence_by_ids(
        repository,
        evidence_ids=question.get("evidence_ids", []),
        expected_hashes=question.get("evidence_sha256", {}),
        expected_manifest_sha256=state["plan_snapshot"].get(
            "corpus_manifest_sha256"
        ),
    )
    if resolution.retrieval_path != "bound_evidence_ids":
        return recent
    return [*recent, *resolution.messages]


def build_durable_interview_graph(
    deps: DurableInterviewGraphDependencies,
    *,
    checkpointer,
):
    builder = StateGraph(DurableInterviewState)
    builder.add_node("initialize_session", initialize_session)
    builder.add_node("project_state", partial(project_state_node, deps=deps))
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
    builder.add_node(
        "prepare_generation", partial(prepare_generation, deps=deps)
    )
    builder.add_node(
        "generate_followup", partial(generate_followup, deps=deps)
    )
    builder.add_node("enqueue_retry", partial(enqueue_retry, deps=deps))
    builder.add_node("wait_for_retry", wait_for_retry)
    builder.add_node("validate_retry", validate_retry)
    builder.add_node("prepare_retry", prepare_retry)
    builder.add_node(
        "commit_interviewer_message", commit_interviewer_message
    )
    builder.add_node("fallback_followup", fallback_followup_node)
    builder.add_node("commit_next_question", commit_next_question)
    builder.add_node(
        "emit_report_event", partial(emit_report_event, deps=deps)
    )
    builder.add_edge(START, "initialize_session")
    builder.add_edge("initialize_session", "project_state")
    builder.add_conditional_edges("project_state", route_after_projection)
    builder.add_edge("wait_for_answer", "validate_command")
    builder.add_conditional_edges(
        "validate_command", route_validated_command
    )
    builder.add_edge("append_candidate_answer", "decide_next_action")
    builder.add_conditional_edges("decide_next_action", route_decision)
    builder.add_edge("prepare_generation", "project_state")
    builder.add_conditional_edges("generate_followup", route_generation)
    builder.add_edge(
        "commit_interviewer_message", "project_state"
    )
    builder.add_edge("enqueue_retry", "wait_for_retry")
    builder.add_edge("wait_for_retry", "validate_retry")
    builder.add_conditional_edges(
        "validate_retry", route_validated_retry
    )
    builder.add_edge("prepare_retry", "generate_followup")
    builder.add_edge("fallback_followup", "commit_interviewer_message")
    builder.add_edge("commit_next_question", "project_state")
    builder.add_edge("apply_skip", "project_state")
    builder.add_edge("apply_finish", "project_state")
    builder.add_edge("emit_report_event", END)
    return builder.compile(checkpointer=checkpointer)
