import ast
from pathlib import Path
from typing import get_type_hints

import pytest
from pydantic import ValidationError

from app.domain.interview.scheduling import (
    CapabilityDescriptor,
    DynamicTaskDeclaration,
    ExecutionState,
    InterviewPlanSlice,
    SchedulerBudget,
    SchedulerContext,
    SchedulerReadyTask,
    SchedulingDecision,
    SchedulingDecisionValidationError,
    TaskRuntimeState,
    validate_scheduling_decision,
)
from app.ports import SchedulingDecisionModelPort


ROOT = Path(__file__).resolve().parents[2]
ACTIONS = {
    "DISPATCH",
    "WAIT_USER",
    "RETRY",
    "ADD_TASK",
    "SKIP_TASK",
    "CANCEL_TASK",
    "COMPLETE",
}


def _ready_task() -> SchedulerReadyTask:
    return SchedulerReadyTask(
        task_id="review-answer",
        capability="interview.evaluation",
        agent_id="interview-reviewer",
        skill="evaluate-answer",
        input_contract="evaluate-answer-request",
        output_contract="evaluation-artifact",
        attempt=0,
        max_attempts=2,
    )


def _capability() -> CapabilityDescriptor:
    return CapabilityDescriptor(
        agent_id="interview-reviewer",
        skill="evaluate-answer",
        description="Evaluate one answer.",
        request_contract_id="evaluate-answer-request",
        request_contract_version="v1",
        output_artifact_type="evaluation-artifact",
        output_artifact_version="1.0",
        capability_version="v1",
    )


def _context(
    *,
    status="READY",
    attempt=0,
    max_attempts=2,
    latest_observation=None,
    remaining_steps=5,
    remaining_task_slots=2,
) -> SchedulerContext:
    task = _ready_task().model_copy(
        update={"attempt": attempt, "max_attempts": max_attempts}
    )
    return SchedulerContext(
        interview_plan_slice=InterviewPlanSlice(
            plan_ref="plan-1",
            plan_revision=1,
        ),
        execution_state=ExecutionState(
            execution_id="exec-1",
            task_states=(
                TaskRuntimeState(
                    task_id=task.task_id,
                    status=status,
                    attempt=attempt,
                    max_attempts=max_attempts,
                ),
            ),
            latest_observation=latest_observation,
            execution_status="RUNNING",
        ),
        ready_tasks=(task,) if status == "READY" else (),
        capabilities=(_capability(),),
        budget=SchedulerBudget(
            max_scheduler_steps=10,
            remaining_scheduler_steps=remaining_steps,
            max_tasks=8,
            remaining_task_slots=remaining_task_slots,
        ),
    )


def test_decision_action_schema_is_closed_to_exactly_seven_actions():
    schema = SchedulingDecision.model_json_schema()
    assert set(schema["properties"]["action"]["enum"]) == ACTIONS

    with pytest.raises(ValidationError, match="Input should be"):
        SchedulingDecision(action="NOOP", reason_code="no_action")
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        SchedulingDecision(
            action="ADD_TASK",
            reason_code="need_evidence",
            add_task=DynamicTaskDeclaration(
                capability="evaluate-answer",
                reason="need evidence",
                request={"arbitrary": True},
            ),
        )


def test_action_shape_requires_and_forbids_task_targets_deterministically():
    for action in ("DISPATCH", "RETRY", "SKIP_TASK", "CANCEL_TASK"):
        with pytest.raises(ValidationError, match=f"{action} requires task_id"):
            SchedulingDecision(action=action, reason_code="missing_target")

    for action in ("ADD_TASK", "COMPLETE"):
        with pytest.raises(ValidationError, match=f"{action} does not accept task_id"):
            SchedulingDecision(
                action=action,
                task_id="review-answer",
                reason_code="unexpected_target",
            )


def test_dispatch_requires_ready_task_compatible_capability_and_budget():
    decision = SchedulingDecision(
        action="DISPATCH",
        task_id="review-answer",
        reason_code="review_ready_answer",
    )
    assert validate_scheduling_decision(_context(), decision) is decision

    with pytest.raises(SchedulingDecisionValidationError) as not_ready:
        validate_scheduling_decision(_context(status="PENDING"), decision)
    assert not_ready.value.code == "task_not_ready"

    context = _context().model_copy(update={"capabilities": ()})
    with pytest.raises(SchedulingDecisionValidationError) as unavailable:
        validate_scheduling_decision(context, decision)
    assert unavailable.value.code == "capability_unavailable"

    with pytest.raises(SchedulingDecisionValidationError) as exhausted:
        validate_scheduling_decision(_context(remaining_steps=0), decision)
    assert exhausted.value.code == "scheduler_step_budget_exhausted"


def test_retry_skip_cancel_and_complete_use_canonical_task_state():
    retry = SchedulingDecision(
        action="RETRY", task_id="review-answer", reason_code="transient_failure"
    )
    assert validate_scheduling_decision(
        _context(status="FAILED", attempt=1, max_attempts=2), retry
    ) is retry
    with pytest.raises(SchedulingDecisionValidationError) as exhausted:
        validate_scheduling_decision(
            _context(status="FAILED", attempt=2, max_attempts=2), retry
        )
    assert exhausted.value.code == "retry_attempts_exhausted"

    skip = SchedulingDecision(
        action="SKIP_TASK", task_id="review-answer", reason_code="not_applicable"
    )
    assert validate_scheduling_decision(_context(status="PENDING"), skip) is skip

    cancel = SchedulingDecision(
        action="CANCEL_TASK", task_id="review-answer", reason_code="superseded"
    )
    with pytest.raises(SchedulingDecisionValidationError) as terminal:
        validate_scheduling_decision(_context(status="COMPLETED"), cancel)
    assert terminal.value.code == "cancel_status_invalid"

    complete = SchedulingDecision(action="COMPLETE", reason_code="work_complete")
    assert validate_scheduling_decision(_context(status="COMPLETED"), complete) is complete
    with pytest.raises(SchedulingDecisionValidationError) as incomplete:
        validate_scheduling_decision(_context(status="READY"), complete)
    assert incomplete.value.code == "tasks_not_terminal"


def test_wait_user_and_add_task_are_validated_without_implementing_dynamic_tasks():
    wait = SchedulingDecision(
        action="WAIT_USER",
        task_id="review-answer",
        reason_code="answer_required",
    )
    context = _context(
        status="COMPLETED",
        latest_observation={
            "task_id": "review-answer",
            "status": "COMPLETED",
            "artifact_type": "main-question-artifact",
        },
    )
    assert validate_scheduling_decision(context, wait) is wait

    add_task = SchedulingDecision(
        action="ADD_TASK",
        reason_code="need_evidence",
        add_task=DynamicTaskDeclaration(
            capability="evaluate-answer",
            reason="need evidence",
        ),
    )
    assert validate_scheduling_decision(_context(), add_task) is add_task
    with pytest.raises(SchedulingDecisionValidationError) as exhausted:
        validate_scheduling_decision(
            _context(remaining_task_slots=0), add_task
        )
    assert exhausted.value.code == "task_budget_exhausted"
    assert set(SchedulingDecision.model_fields) == {
        "action",
        "task_id",
        "add_task",
        "reason_code",
    }


def test_model_port_now_resolves_concrete_context_and_decision_types():
    hints = get_type_hints(SchedulingDecisionModelPort.decide)
    assert hints["context"] is SchedulerContext
    assert hints["return"] is SchedulingDecision


def test_decision_contract_is_pure_domain_without_provider_dependencies():
    path = ROOT / "app" / "domain" / "interview" / "scheduling" / "decisions.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert all(
        not module.startswith(
            ("app.adapters", "app.a2a", "app.runtime", "openai", "anthropic")
        )
        for module in imported_modules
    )
