import ast
from pathlib import Path

import pytest

from app.domain.interview.scheduling import (
    BoundedLoopExceeded,
    CapabilityDescriptor,
    DynamicTaskDeclaration,
    ExecutionState,
    InterviewPlanSlice,
    SchedulerBudget,
    SchedulerContext,
    SchedulerReadyTask,
    SchedulingDecision,
    TaskRuntimeState,
    validate_bounded_loop,
    validate_scheduling_decision,
)


ROOT = Path(__file__).resolve().parents[2]


def _context(**budget_changes) -> SchedulerContext:
    task = SchedulerReadyTask(
        task_id="review",
        capability="interview.evaluation",
        agent_id="interview-reviewer",
        skill="evaluate-answer",
        input_contract="evaluate-answer-request",
        output_contract="evaluation-artifact",
        attempt=0,
        max_attempts=2,
    )
    values = {
        "max_scheduler_steps": 20,
        "remaining_scheduler_steps": 10,
        "max_agent_calls": 10,
        "remaining_agent_calls": 10,
        "max_replans": 5,
        "remaining_replans": 5,
        "max_retries": 5,
        "remaining_retries": 5,
        "max_followups": 5,
        "remaining_followups": 5,
        "max_questions": 5,
        "remaining_questions": 5,
        "execution_timeout_seconds": 300,
        "elapsed_execution_seconds": 10,
    }
    values.update(budget_changes)
    for name in (
        "agent_calls",
        "replans",
        "retries",
        "followups",
        "questions",
    ):
        if f"max_{name}" in budget_changes and f"remaining_{name}" not in budget_changes:
            values[f"remaining_{name}"] = 0
    return SchedulerContext(
        interview_plan_slice=InterviewPlanSlice(plan_ref="plan-1", plan_revision=1),
        execution_state=ExecutionState(
            execution_id="exec-1",
                task_states=(
                    TaskRuntimeState(task_id="review", status="READY", max_attempts=2),
                ),
            scheduler_step_count=0,
        ),
        ready_tasks=(task,),
        capabilities=(
            CapabilityDescriptor(
                agent_id="interview-reviewer",
                skill="evaluate-answer",
                description="Review answer.",
                request_contract_id="evaluate-answer-request",
                request_contract_version="v1",
                output_artifact_type="evaluation-artifact",
                output_artifact_version="1.0",
                capability_version="v1",
            ),
        ),
        budget=SchedulerBudget(**values),
    )


def _decision(action: str, **changes) -> SchedulingDecision:
    values = {"action": action, "reason_code": "bounded_test"}
    if action in {"DISPATCH", "RETRY", "SKIP_TASK", "CANCEL_TASK"}:
        values["task_id"] = "review"
    values.update(changes)
    return SchedulingDecision(**values)


def _add_task(capability: str = "generate-followup", reason_code: str = "replan"):
    return SchedulingDecision(
        action="ADD_TASK",
        reason_code=reason_code,
        add_task=DynamicTaskDeclaration(
            capability=capability,
            reason="bounded-loop test",
        ),
    )


@pytest.mark.parametrize(
    ("budget_changes", "decision", "code"),
    (
        (
            {"remaining_scheduler_steps": 0},
            _decision("COMPLETE"),
            "scheduler_step_budget_exhausted",
        ),
        (
            {"max_agent_calls": 1, "agent_calls_used": 1},
            _decision("DISPATCH"),
            "agent_calls_exhausted",
        ),
        (
            {"max_replans": 1, "replans_used": 1},
            _add_task(),
            "replan_budget_exhausted",
        ),
        (
            {"max_retries": 1, "retries_used": 1},
            _decision("RETRY"),
            "retry_budget_exhausted",
        ),
        (
            {"max_followups": 1, "followups_used": 1},
            _add_task(reason_code="adaptive_followup"),
            "followup_budget_exhausted",
        ),
        (
            {"max_questions": 1, "questions_used": 1},
            _add_task(capability="generate-main-question", reason_code="question"),
            "question_budget_exhausted",
        ),
        (
            {"execution_timeout_seconds": 10, "elapsed_execution_seconds": 10},
            _decision("WAIT_USER"),
            "execution_timeout_exhausted",
        ),
    ),
)
def test_each_bounded_loop_limit_fails_closed(budget_changes, decision, code):
    with pytest.raises(BoundedLoopExceeded) as error:
        validate_bounded_loop(_context(**budget_changes), decision)
    assert error.value.code == code
    assert error.value.limit


def test_all_bounded_limits_allow_a_step_when_capacity_remains():
    context = _context()
    validate_bounded_loop(context, _decision("DISPATCH"))
    assert validate_scheduling_decision(context, _decision("DISPATCH")).action == "DISPATCH"


def test_budget_model_rejects_inconsistent_used_remaining_and_timeout_values():
    with pytest.raises(ValueError, match="used plus remaining agent calls"):
        SchedulerBudget(max_agent_calls=2, agent_calls_used=1, remaining_agent_calls=2)
    with pytest.raises(ValueError, match="elapsed execution time"):
        SchedulerBudget(execution_timeout_seconds=5, elapsed_execution_seconds=6)


def test_bounded_loop_validator_is_domain_only():
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
