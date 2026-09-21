from __future__ import annotations

import ast
from pathlib import Path

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from app.domain.interview.scheduling import ExecutionState, WaitHandle
from app.graphs.scheduler_graph import (
    SchedulerGraphDependencies,
    build_scheduler_graph,
)


ROOT = Path(__file__).resolve().parents[2]


def _waiting_state(state: ExecutionState) -> ExecutionState:
    issued_revision = state.revision + 1
    return state.apply_transition(
        expected_revision=state.revision,
        transition_name="test:observe",
        execution_status="WAITING",
        current_wait_handle=WaitHandle(
            wait_id=f"wait:{state.execution_id}:{issued_revision}",
            execution_id=state.execution_id,
            task_id="question-1",
            question_id="q1",
            issued_revision=issued_revision,
            expected_command_kind="ANSWER",
        ),
        latest_observation={"status": "WAITING", "task_id": "question-1"},
    )


def _dependencies(events: list[str]) -> SchedulerGraphDependencies:
    def decide(state):
        events.append("DECIDE")
        if state.execution_status == "PENDING":
            return "DISPATCH"
        if state.execution_status == "WAITING":
            return "WAIT_USER"
        return "COMPLETE"

    def dispatch(state):
        events.append("DISPATCH")
        return state.apply_transition(
            expected_revision=state.revision,
            transition_name="test:dispatch",
            execution_status="RUNNING",
        )

    def observe(state):
        events.append("OBSERVE")
        return _waiting_state(state)

    def resume_wait(state, payload):
        events.append("WAIT_USER")
        assert payload == {"command_id": "cmd-1"}
        return state.apply_transition(
            expected_revision=state.revision,
            transition_name="test:resume",
            execution_status="COMPLETED",
            current_wait_handle=None,
            latest_observation={"status": "ANSWER_RECEIVED"},
        )

    def complete(state):
        events.append("COMPLETE")
        return state

    return SchedulerGraphDependencies(
        decide=decide,
        dispatch=dispatch,
        observe=observe,
        resume_wait=resume_wait,
        complete=complete,
    )


def test_minimal_scheduler_graph_runs_generic_phases_and_resumes_wait():
    events: list[str] = []
    graph = build_scheduler_graph(
        _dependencies(events),
        checkpointer=InMemorySaver(),
    )
    config = {"configurable": {"thread_id": "exec-graph-1"}}
    initial = ExecutionState(execution_id="exec-graph-1")

    graph.invoke(
        {"execution_state": initial.model_dump(mode="json")},
        config,
    )
    snapshot = graph.get_state(config)

    assert snapshot.next == ("WAIT_USER",)
    assert snapshot.values.keys() == {"execution_state"}
    persisted = ExecutionState.model_validate(snapshot.values["execution_state"])
    assert persisted.current_wait_handle is not None
    assert events == ["DECIDE", "DISPATCH", "OBSERVE", "DECIDE"]

    final = graph.invoke(Command(resume={"command_id": "cmd-1"}), config)

    assert ExecutionState.model_validate(final["execution_state"]).execution_status == "COMPLETED"
    assert events == [
        "DECIDE",
        "DISPATCH",
        "OBSERVE",
        "DECIDE",
        "WAIT_USER",
        "DECIDE",
        "COMPLETE",
    ]


def test_scheduler_graph_topology_has_only_generic_orchestration_nodes():
    graph = build_scheduler_graph(
        _dependencies([]),
        checkpointer=InMemorySaver(),
    )

    node_names = set(graph.get_graph().nodes) - {"__start__", "__end__"}
    assert node_names == {"DECIDE", "DISPATCH", "OBSERVE", "WAIT_USER", "COMPLETE"}
    assert not any(
        token in name.lower()
        for name in node_names
        for token in ("examiner", "reviewer", "coach", "knowledge", "report")
    )


def test_scheduler_graph_does_not_import_agent_or_transport_implementations():
    path = ROOT / "app" / "graphs" / "scheduler_graph.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }

    assert all(not module.startswith("app.a2a") for module in modules)
    assert all(not module.startswith("app.agents") for module in modules)

