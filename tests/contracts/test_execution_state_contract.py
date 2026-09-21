import ast
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.domain.interview.scheduling import (
    ExecutionArtifactRef,
    ExecutionState,
    ExecutionStateConflict,
    ExecutionTaskDefinition,
    TaskRuntimeState,
    WaitHandle,
)


ROOT = Path(__file__).resolve().parents[2]


def _dynamic_task(task_id: str) -> ExecutionTaskDefinition:
    return ExecutionTaskDefinition(
        task_id=task_id,
        capability="interview.followup",
        agent_id="interview-examiner",
        skill="generate-followup",
    )


def test_execution_state_contains_runtime_truth_and_derived_pending_projection():
    state = ExecutionState(
        execution_id="exec-1",
        task_states=(
            TaskRuntimeState(task_id="a", status="READY"),
            TaskRuntimeState(task_id="b", status="COMPLETED", attempt=1),
        ),
        task_attempts={"a": 0, "b": 1},
        dynamic_task_definitions=(_dynamic_task("followup-1"),),
        artifact_refs=(
            ExecutionArtifactRef(
                artifact_ref="artifact:eval:1",
                artifact_type="evaluation-artifact",
                task_id="b",
            ),
        ),
        current_wait_handle=WaitHandle(
            wait_id="wait-1",
            execution_id="exec-1",
            task_id="a",
            question_id="q1",
            issued_revision=0,
            expected_command_kind="answer",
        ),
        latest_observation={"source": "agent", "event": "answer_received"},
        scheduler_step_count=4,
        execution_status="WAITING",
    )

    assert state.revision == 0
    assert state.pending_task_ids() == ("a",)
    assert state.dynamic_task_definitions[0].task_id == "followup-1"
    assert state.current_wait_handle.wait_id == "wait-1"


def test_execution_state_transition_is_revision_cas_and_immutable():
    state = ExecutionState(execution_id="exec-1")
    next_state = state.apply_transition(
        expected_revision=0,
        transition_name="start_execution",
        execution_status="RUNNING",
        scheduler_step_count=1,
    )

    assert state.revision == 0
    assert next_state.revision == 1
    assert next_state.execution_status == "RUNNING"
    with pytest.raises(ExecutionStateConflict, match="revision conflict"):
        next_state.cas_update(
            expected_revision=0,
            execution_status="COMPLETED",
        )
    with pytest.raises(ValidationError):
        next_state.execution_status = "FAILED"


def test_execution_state_rejects_unknown_or_duplicate_runtime_facts():
    with pytest.raises(ValidationError, match="task runtime task_id values"):
        ExecutionState(
            execution_id="exec-1",
            task_states=(
                TaskRuntimeState(task_id="same"),
                TaskRuntimeState(task_id="same"),
            ),
        )

    state = ExecutionState(execution_id="exec-1")
    with pytest.raises(ValueError, match="immutable or unknown"):
        state.apply_transition(
            expected_revision=0,
            transition_name="illegal_projection_write",
            pending_tasks=("a",),
        )
    with pytest.raises(ValidationError):
        state.apply_transition(
            expected_revision=0,
            transition_name="invalid_status",
            execution_status="NOT_A_STATUS",
        )


def test_execution_state_is_pure_domain_and_has_no_duplicate_fact_projections():
    path = ROOT / "app" / "domain" / "interview" / "scheduling" / "state.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert all(
        not module.startswith(("app.adapters", "app.a2a", "app.runtime"))
        for module in imported_modules
    )
    assert "pending_tasks" not in ExecutionState.model_fields
