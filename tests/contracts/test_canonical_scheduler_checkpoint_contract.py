from __future__ import annotations

from langgraph.checkpoint.memory import InMemorySaver
from pathlib import Path
from pydantic import ValidationError
import pytest

from app.domain.interview.scheduling import (
    ExecutionArtifactRef,
    ExecutionState,
    TaskRuntimeState,
    WaitHandle,
)
from app.graphs.scheduler_graph import (
    SchedulerGraphDependencies,
    SchedulerGraphState,
    build_scheduler_graph,
    deserialize_execution_state,
    scheduler_graph_input,
    serialize_execution_state,
)


ROOT = Path(__file__).resolve().parents[2]


def _aggregate() -> ExecutionState:
    return ExecutionState(
        execution_id="exec-canonical",
        revision=7,
        task_states=(
            TaskRuntimeState(task_id="done", status="COMPLETED", attempt=1),
            TaskRuntimeState(task_id="pending", status="PENDING"),
        ),
        task_attempts={"done": 1, "pending": 0},
        artifact_refs=(
            ExecutionArtifactRef(
                artifact_ref="artifact:done:1",
                artifact_type="evaluation-artifact",
                task_id="done",
            ),
        ),
        current_wait_handle=WaitHandle(
            wait_id="wait-7",
            execution_id="exec-canonical",
            task_id="pending",
            question_id="q1",
            issued_revision=7,
            expected_command_kind="ANSWER",
        ),
        latest_observation={"status": "WAITING", "task_id": "pending"},
        scheduler_step_count=4,
        execution_status="WAITING",
    )


def test_checkpoint_serialization_round_trips_only_execution_state_fields():
    state = _aggregate()

    serialized = serialize_execution_state(state)
    restored = deserialize_execution_state(serialized)

    assert restored == state
    assert set(serialized) == set(ExecutionState.model_fields)
    assert "completed_task_ids" not in serialized
    assert "pending_task_ids" not in serialized
    assert restored.pending_task_ids() == ("pending",)


def test_checkpoint_envelope_has_exactly_one_canonical_field():
    assert set(SchedulerGraphState.__annotations__) == {"execution_state"}
    assert scheduler_graph_input(_aggregate()).keys() == {"execution_state"}


def test_graph_runtime_does_not_define_shadow_task_fact_sources():
    source = (ROOT / "app" / "graphs" / "scheduler_graph.py").read_text(
        encoding="utf-8"
    )

    assert "completed_task_ids" not in source
    assert "pending_task_ids" not in source


@pytest.mark.parametrize("shadow_field", ["completed_task_ids", "pending_task_ids"])
def test_deserializer_rejects_shadow_task_fact_sources(shadow_field):
    serialized = serialize_execution_state(_aggregate())
    serialized[shadow_field] = ["done"]

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        deserialize_execution_state(serialized)


def test_new_graph_instance_reads_the_same_canonical_checkpoint():
    saver = InMemorySaver()
    state = _aggregate()
    deps = SchedulerGraphDependencies(
        decide=lambda _state: "NOOP",
        dispatch=lambda current: current,
        observe=lambda current: current,
        resume_wait=lambda current, _payload: current,
    )
    config = {"configurable": {"thread_id": state.execution_id}}

    first = build_scheduler_graph(deps, checkpointer=saver)
    first.invoke(scheduler_graph_input(state), config)
    rebuilt = build_scheduler_graph(deps, checkpointer=saver)
    snapshot = rebuilt.get_state(config)

    assert snapshot.values.keys() == {"execution_state"}
    assert deserialize_execution_state(snapshot.values["execution_state"]) == state
