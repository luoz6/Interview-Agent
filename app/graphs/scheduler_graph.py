"""Minimal durable LangGraph runtime for the deterministic Scheduler.

The graph owns only orchestration phases.  Agent identities, skills, and task
topology remain data in ``ExecutionPlan``/``ExecutionState`` and never become
LangGraph nodes.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import partial
from typing import Any, Callable, Literal, Mapping, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.domain.interview.scheduling.state import ExecutionState
from app.domain.interview.scheduling.commands import UserCommand
from app.domain.interview.scheduling.plan import ExecutionPlan


SchedulerGraphAction = Literal[
    "DISPATCH",
    "WAIT_USER",
    "COMPLETE",
    "NOOP",
    "FAILED",
]


class SchedulerGraphState(TypedDict):
    """Checkpoint envelope containing only the canonical aggregate."""

    execution_state: dict[str, Any]


@dataclass(frozen=True)
class SchedulerGraphDependencies:
    """Application callbacks used by the transport-neutral graph shell."""

    decide: Callable[[ExecutionState], Any]
    dispatch: Callable[[ExecutionState], ExecutionState]
    observe: Callable[[ExecutionState], ExecutionState]
    resume_wait: Callable[[ExecutionState, Mapping[str, Any]], ExecutionState]
    complete: Callable[[ExecutionState], ExecutionState] = lambda state: state


def scheduler_application_dependencies(
    scheduler: Any,
    plan: ExecutionPlan,
    *,
    execution_context: Any | None = None,
) -> SchedulerGraphDependencies:
    """Bind the generic graph phases to a Scheduler application capability."""

    def decide(state: ExecutionState) -> Any:
        return scheduler.policy.decide(plan=plan, state=state)

    def dispatch(state: ExecutionState) -> ExecutionState:
        return scheduler.run_once(
            plan=plan,
            state=state,
            execution_context=execution_context,
        ).state

    def observe(state: ExecutionState) -> ExecutionState:
        decision = scheduler.policy.decide(plan=plan, state=state)
        if (
            decision.action == "WAIT_USER"
            and state.current_wait_handle is None
            and decision.reason_code == "user_answer_required"
        ):
            return scheduler.run_once(
                plan=plan,
                state=state,
                execution_context=execution_context,
            ).state
        return state

    def resume_wait(
        state: ExecutionState,
        payload: Mapping[str, Any],
    ) -> ExecutionState:
        command_payload = payload.get("command", payload)
        command = UserCommand.model_validate(command_payload)
        return scheduler.apply_user_command(state, command).state

    return SchedulerGraphDependencies(
        decide=decide,
        dispatch=dispatch,
        observe=observe,
        resume_wait=resume_wait,
    )


def serialize_execution_state(state: ExecutionState) -> dict[str, Any]:
    """Return the stable JSON-compatible checkpoint representation."""

    if not isinstance(state, ExecutionState):
        raise TypeError("scheduler checkpoint state must be ExecutionState")
    return state.model_dump(mode="json", round_trip=True)


def deserialize_execution_state(payload: Mapping[str, Any]) -> ExecutionState:
    """Restore and strictly validate the canonical aggregate."""

    if not isinstance(payload, Mapping):
        raise TypeError("serialized execution state must be a mapping")
    return ExecutionState.model_validate(dict(payload))


def scheduler_graph_input(state: ExecutionState) -> SchedulerGraphState:
    """Construct the only supported checkpoint envelope."""

    return {"execution_state": serialize_execution_state(state)}


def _aggregate(state: SchedulerGraphState) -> ExecutionState:
    return deserialize_execution_state(state["execution_state"])


def _serialized(state: ExecutionState) -> SchedulerGraphState:
    return scheduler_graph_input(state)


def decide_node(_state: SchedulerGraphState) -> dict[str, Any]:
    """Durable scheduling boundary; routing recomputes a pure decision."""

    return {}


def route_after_decide(
    state: SchedulerGraphState,
    deps: SchedulerGraphDependencies,
) -> str:
    decision = deps.decide(_aggregate(state))
    action = getattr(decision, "action", decision)
    if action not in {"DISPATCH", "WAIT_USER", "COMPLETE", "NOOP", "FAILED"}:
        raise ValueError(f"unsupported scheduler graph action: {action}")
    return "END" if action in {"NOOP", "FAILED"} else action


def dispatch_node(
    state: SchedulerGraphState,
    deps: SchedulerGraphDependencies,
) -> SchedulerGraphState:
    current = _aggregate(state)
    return _serialized(_same_execution(current, deps.dispatch(current)))


def observe_node(
    state: SchedulerGraphState,
    deps: SchedulerGraphDependencies,
) -> SchedulerGraphState:
    current = _aggregate(state)
    return _serialized(_same_execution(current, deps.observe(current)))


def wait_user_node(
    state: SchedulerGraphState,
    deps: SchedulerGraphDependencies,
) -> SchedulerGraphState:
    current = _aggregate(state)
    wait = current.current_wait_handle
    payload = interrupt(
        {
            "kind": "scheduler_wait_user",
            "execution_id": current.execution_id,
            "revision": current.revision,
            "wait_handle": (
                wait.model_dump(mode="json") if wait is not None else None
            ),
        }
    )
    if not isinstance(payload, Mapping):
        raise TypeError("scheduler WAIT_USER resume payload must be a mapping")
    return _serialized(
        _same_execution(current, deps.resume_wait(current, payload))
    )


def complete_node(
    state: SchedulerGraphState,
    deps: SchedulerGraphDependencies,
) -> SchedulerGraphState:
    current = _aggregate(state)
    return _serialized(_same_execution(current, deps.complete(current)))


def _same_execution(current: ExecutionState, updated: ExecutionState) -> ExecutionState:
    if not isinstance(updated, ExecutionState):
        raise TypeError("scheduler graph callbacks must return ExecutionState")
    if updated.execution_id != current.execution_id:
        raise ValueError("scheduler graph callback changed execution identity")
    return updated


def build_scheduler_graph(
    deps: SchedulerGraphDependencies,
    *,
    checkpointer: Any,
):
    """Compile the five-node Scheduler runtime with durable checkpoints."""

    builder = StateGraph(SchedulerGraphState)
    builder.add_node("DECIDE", decide_node)
    builder.add_node("DISPATCH", partial(dispatch_node, deps=deps))
    builder.add_node("OBSERVE", partial(observe_node, deps=deps))
    builder.add_node("WAIT_USER", partial(wait_user_node, deps=deps))
    builder.add_node("COMPLETE", partial(complete_node, deps=deps))

    builder.add_edge(START, "DECIDE")
    builder.add_conditional_edges(
        "DECIDE",
        partial(route_after_decide, deps=deps),
        {
            "DISPATCH": "DISPATCH",
            "WAIT_USER": "WAIT_USER",
            "COMPLETE": "COMPLETE",
            "END": END,
        },
    )
    builder.add_edge("DISPATCH", "OBSERVE")
    builder.add_edge("OBSERVE", "DECIDE")
    builder.add_edge("WAIT_USER", "DECIDE")
    builder.add_edge("COMPLETE", END)
    return builder.compile(checkpointer=checkpointer)


__all__ = [
    "SchedulerGraphAction",
    "SchedulerGraphDependencies",
    "SchedulerGraphState",
    "build_scheduler_graph",
    "deserialize_execution_state",
    "scheduler_graph_input",
    "scheduler_application_dependencies",
    "serialize_execution_state",
]
