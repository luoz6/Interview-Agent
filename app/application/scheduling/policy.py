"""Deterministic interview scheduling decisions.

The policy intentionally reasons only from the immutable plan and canonical
ExecutionState.  It does not ask an LLM to choose the next task; question,
answer, evaluation, follow-up, and report ordering is represented by plan
dependencies plus the explicit WAIT_USER fence.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domain.interview.scheduling.plan import ExecutionPlan, ExecutionTaskDefinition
from app.domain.interview.scheduling.state import ExecutionState


SchedulerPolicyAction = Literal["DISPATCH", "WAIT_USER", "COMPLETE", "NOOP"]


class SchedulerDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    action: SchedulerPolicyAction
    task_id: str | None = Field(default=None, min_length=1)
    reason_code: str = Field(min_length=1)


class DeterministicSchedulerPolicy:
    """Choose one valid next action without autonomous planning."""

    def decide(
        self,
        *,
        plan: ExecutionPlan,
        state: ExecutionState,
    ) -> SchedulerDecision:
        if plan.execution_id != state.execution_id:
            return SchedulerDecision(
                action="NOOP",
                reason_code="execution_identity_mismatch",
            )

        if state.current_wait_handle is not None:
            return SchedulerDecision(
                action="WAIT_USER",
                task_id=state.current_wait_handle.task_id,
                reason_code="wait_handle_active",
            )

        if any(task.status == "WAITING" for task in state.task_states):
            return SchedulerDecision(
                action="WAIT_USER",
                reason_code="task_waiting_for_user",
            )

        if any(task.status == "RUNNING" for task in state.task_states):
            return SchedulerDecision(
                action="NOOP",
                reason_code="awaiting_observation",
            )

        # A generated main question or follow-up is a user-facing boundary.
        # Once its artifact is observed, the next scheduler turn must wait for
        # the fenced command instead of dispatching evaluation immediately.
        observation = state.latest_observation or {}
        if (
            observation.get("status") == "COMPLETED"
            and observation.get("artifact_type")
            in {"main-question-artifact", "followup-artifact"}
            and _observation_requires_user_wait(plan, state, observation)
        ):
            return SchedulerDecision(
                action="WAIT_USER",
                reason_code="user_answer_required",
            )

        task_definitions = plan.task_definitions + state.dynamic_task_definitions
        completed = {
            item.task_id
            for item in state.task_states
            if item.status in {"COMPLETED", "SKIPPED"}
        }
        runtime_by_id = {item.task_id: item for item in state.task_states}
        for task in task_definitions:
            runtime = runtime_by_id.get(task.task_id)
            if runtime is None or runtime.status not in {"PENDING", "READY"}:
                continue
            predecessors = {
                dependency.predecessor_task_id
                for dependency in plan.dependency_definitions
                if dependency.successor_task_id == task.task_id
            }
            if task in state.dynamic_task_definitions:
                predecessors.update(
                    dependency
                    for dependency in task.parameters.get("dependencies", ())
                    if isinstance(dependency, str) and dependency
                )
            if predecessors <= completed:
                return SchedulerDecision(
                    action="DISPATCH",
                    task_id=task.task_id,
                    reason_code=_phase_reason(task),
                )

        if all(
            runtime_by_id.get(task.task_id) is not None
            and runtime_by_id[task.task_id].status
            in {"COMPLETED", "SKIPPED", "CANCELED"}
            for task in task_definitions
        ):
            return SchedulerDecision(action="COMPLETE", reason_code="all_tasks_terminal")
        return SchedulerDecision(action="NOOP", reason_code="no_valid_next_action")


def _phase_reason(task: ExecutionTaskDefinition) -> str:
    explicit_phase = task.parameters.get("phase")
    if isinstance(explicit_phase, str) and explicit_phase.strip():
        return f"dispatch_{explicit_phase.strip()}"
    return {
        "generate-main-question": "dispatch_main_question",
        "evaluate-answer": "dispatch_evaluation",
        "generate-followup": "dispatch_followup",
        "evaluate-interview": "dispatch_final_evaluation",
        "generate-report": "dispatch_report",
    }.get(task.skill, "dispatch_task")


def _observation_requires_user_wait(
    plan: ExecutionPlan,
    state: ExecutionState,
    observation: dict,
) -> bool:
    task_id = observation.get("task_id")
    if not isinstance(task_id, str) or not task_id:
        return False
    # A user wait is part of a question/evaluation chain.  A standalone
    # adapter task (useful for smoke tests and maintenance jobs) completes
    # synchronously and must not acquire a phantom WAIT_USER state.
    if any(
        dependency.predecessor_task_id == task_id
        for dependency in plan.dependency_definitions
    ):
        return True
    return any(
        task.task_id != task_id
        and task.skill in {"evaluate-answer", "evaluate-interview", "generate-report"}
        for task in plan.task_definitions + state.dynamic_task_definitions
    )


__all__ = [
    "DeterministicSchedulerPolicy",
    "SchedulerDecision",
    "SchedulerPolicyAction",
]
