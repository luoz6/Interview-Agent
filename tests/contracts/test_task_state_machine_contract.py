import pytest

from app.domain.interview.scheduling import (
    ExecutionState,
    ExecutionStateConflict,
    InvalidTaskTransition,
    TaskRuntimeState,
    transition_task_state,
)


def test_task_state_machine_accepts_happy_path_and_retry():
    task = TaskRuntimeState(task_id="task-1", max_attempts=2)
    task = transition_task_state(task, "READY")
    task = transition_task_state(task, "RUNNING")
    assert task.status == "RUNNING"
    assert task.attempt == 1
    task = transition_task_state(task, "FAILED", reason_code="provider_timeout")
    assert task.reason_code == "provider_timeout"
    task = transition_task_state(task, "READY")
    task = transition_task_state(task, "RUNNING")
    assert task.attempt == 2
    task = transition_task_state(task, "COMPLETED")
    assert task.status == "COMPLETED"


def test_task_state_machine_rejects_illegal_transitions_and_exhausted_retry():
    with pytest.raises(InvalidTaskTransition):
        transition_task_state(TaskRuntimeState(task_id="task-1"), "RUNNING")
    with pytest.raises(InvalidTaskTransition):
        transition_task_state(
            TaskRuntimeState(task_id="task-1", status="COMPLETED"),
            "RUNNING",
        )
    exhausted = TaskRuntimeState(
        task_id="task-1",
        status="FAILED",
        attempt=1,
        max_attempts=1,
    )
    with pytest.raises(ValueError, match="max_attempts"):
        transition_task_state(exhausted, "READY")


def test_execution_state_task_transition_uses_cas_and_cannot_be_bypassed():
    state = ExecutionState(
        execution_id="exec-1",
        task_states=(TaskRuntimeState(task_id="task-1"),),
    )
    ready = state.transition_task(
        expected_revision=0,
        task_id="task-1",
        target_status="READY",
    )
    running = ready.transition_task(
        expected_revision=1,
        task_id="task-1",
        target_status="RUNNING",
    )
    assert running.revision == 2
    assert running.task_state("task-1").attempt == 1
    assert running.task_attempts == {"task-1": 1}
    with pytest.raises(ExecutionStateConflict):
        running.transition_task(
            expected_revision=1,
            task_id="task-1",
            target_status="COMPLETED",
        )
    with pytest.raises(ValueError, match="transition_task"):
        running.apply_transition(
            expected_revision=2,
            transition_name="bypass",
            task_states=(),
        )
