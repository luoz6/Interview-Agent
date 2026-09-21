from types import SimpleNamespace

import pytest

import app.runtime.composition as runtime
from app.domain.interview.orchestration_cutover import ExecutionPathConflict
from app.domain.interview.scheduling import (
    ExecutionPlan,
    ExecutionState,
    ExecutionTaskDefinition,
    TaskRuntimeState,
)
from app.ports import (
    AgentCapabilityPort,
    AgentInvocationLedgerPort,
    AgentInvocationPort,
)


def setup_function():
    runtime.reset_runtime_for_tests()


def teardown_function():
    runtime.reset_runtime_for_tests()


def _configure_memory_runtime(monkeypatch):
    llm = object()
    knowledge = object()
    execution_runner = object()
    session_store = SimpleNamespace(llm=llm)
    monkeypatch.setattr(runtime, "get_runtime_store", lambda: "memory")
    monkeypatch.setattr(runtime, "get_session_store", lambda: session_store)
    monkeypatch.setattr(runtime, "resolve_runtime_llm", lambda store: llm)
    monkeypatch.setattr(runtime, "get_runtime_knowledge_repository", lambda: knowledge)
    monkeypatch.setattr(runtime, "get_agent_execution_runner", lambda: execution_runner)
    monkeypatch.setattr(runtime, "get_user_document_store", lambda: object())
    return llm, knowledge, execution_runner


def _plan_and_state():
    task = ExecutionTaskDefinition(
        task_id="followup-1",
        capability="interview.followup",
        agent_id="interview-examiner",
        skill="generate-followup",
        input_contract="generate-followup-request",
        output_contract="followup-artifact",
        parameters={"question_id": "q1", "focus": "tradeoffs"},
    )
    plan = ExecutionPlan(
        execution_id="execution-1",
        interview_plan_ref="plan-1",
        task_definitions=(task,),
    )
    state = ExecutionState(
        execution_id="execution-1",
        task_states=(TaskRuntimeState(task_id="followup-1"),),
    )
    return plan, state


def test_runtime_container_owns_one_a2a_and_professional_agent_composition(monkeypatch):
    llm, knowledge, execution_runner = _configure_memory_runtime(monkeypatch)
    import app.a2a.runtime as a2a_runtime_module

    captured = {}
    actual_builder = a2a_runtime_module.build_local_a2a_runtime

    def recording_builder(**kwargs):
        captured.update(kwargs)
        return actual_builder(**kwargs)

    monkeypatch.setattr(
        a2a_runtime_module,
        "build_local_a2a_runtime",
        recording_builder,
    )

    first = runtime.get_a2a_runtime()
    second = runtime.get_a2a_runtime()

    assert first is second
    assert isinstance(first.registry, AgentCapabilityPort)
    assert isinstance(first.invoker, AgentInvocationPort)
    assert {agent_id for agent_id, _skill in first.server.registered_skills} == {
        "interview-examiner",
        "interview-reviewer",
        "knowledge-and-grounding",
        "report-coach",
    }
    assert runtime.get_runtime_container().require("a2a_runtime") is first
    assert captured["llm"] is llm
    assert captured["vector_store"] is knowledge
    assert captured["execution_runner"] is execution_runner
    assert captured["user_document_store_getter"] is runtime.get_user_document_store


def test_scheduler_bundle_uses_one_composed_dependency_graph(monkeypatch):
    _configure_memory_runtime(monkeypatch)
    plan, state = _plan_and_state()

    composed = runtime.compose_scheduler_runtime(plan=plan, initial_state=state)

    assert composed.a2a_runtime is runtime.get_a2a_runtime()
    assert composed.capability_adapter is composed.a2a_runtime.registry
    assert composed.invocation_adapter is composed.a2a_runtime.invoker
    assert composed.scheduler.capability_port is composed.capability_adapter
    assert composed.scheduler.invocation_port is composed.invocation_adapter
    assert composed.scheduler.invocation_ledger is composed.durable_ledger
    assert isinstance(composed.durable_ledger, AgentInvocationLedgerPort)
    assert composed.durable_ledger is runtime.get_scheduler_invocation_ledger()
    assert composed.memory_store is runtime.get_agent_memory_store()
    assert composed.execution_state_store is runtime.get_scheduler_execution_state_store()
    assert composed.checkpointer is runtime.get_scheduler_checkpointer()
    assert (
        composed.execution_path_binding_store
        is runtime.get_execution_path_binding_store()
    )
    assert (
        composed.execution_path_binding_store.get(state.execution_id).path
        == "NEW"
    )
    assert composed.execution_state_store.load(state.execution_id) == state

    node_names = set(composed.graph.get_graph().nodes) - {"__start__", "__end__"}
    assert node_names == {"DECIDE", "DISPATCH", "OBSERVE", "WAIT_USER", "COMPLETE"}


def test_composition_rejects_execution_identity_or_state_conflicts(monkeypatch):
    _configure_memory_runtime(monkeypatch)
    plan, state = _plan_and_state()
    runtime.compose_scheduler_runtime(plan=plan, initial_state=state)

    conflicting = state.apply_transition(
        expected_revision=state.revision,
        transition_name="test:conflict",
        execution_status="RUNNING",
    )
    with pytest.raises(RuntimeError, match="already composed"):
        runtime.compose_scheduler_runtime(plan=plan, initial_state=conflicting)

    wrong_state = ExecutionState(execution_id="other-execution")
    with pytest.raises(ValueError, match="identities differ"):
        runtime.compose_scheduler_runtime(plan=plan, initial_state=wrong_state)


def test_scheduler_composition_rejects_execution_owned_by_old_path(monkeypatch):
    _configure_memory_runtime(monkeypatch)
    plan, state = _plan_and_state()
    runtime.get_execution_path_binding_store().bind(state.execution_id, "OLD")

    with pytest.raises(ExecutionPathConflict):
        runtime.compose_scheduler_runtime(plan=plan, initial_state=state)
