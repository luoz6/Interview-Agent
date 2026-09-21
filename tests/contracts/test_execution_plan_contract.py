import ast
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.domain.interview.scheduling import (
    ExecutionConstraints,
    ExecutionDependencyDefinition,
    ExecutionPlan,
    ExecutionTaskDefinition,
)


ROOT = Path(__file__).resolve().parents[2]


def _task(task_id: str) -> ExecutionTaskDefinition:
    return ExecutionTaskDefinition(
        task_id=task_id,
        capability="interview.evaluation",
        agent_id="interview-reviewer",
        skill="evaluate-answer",
        output_contract="evaluation-artifact",
    )


def test_execution_plan_contains_definition_only():
    plan = ExecutionPlan(
        execution_id="exec-1",
        interview_plan_ref="plan-revision-7",
        definition_revision=3,
        task_definitions=(_task("evaluate"), _task("report")),
        dependency_definitions=(
            ExecutionDependencyDefinition(
                predecessor_task_id="evaluate",
                successor_task_id="report",
            ),
        ),
        execution_constraints=ExecutionConstraints(max_tasks=2),
    )

    assert plan.revision == 3
    assert plan.tasks[0].task_id == "evaluate"
    assert plan.dependencies[0].successor_task_id == "report"
    assert plan.constraints.max_tasks == 2
    assert "status" not in type(plan).model_fields
    assert "attempt" not in type(plan).model_fields
    assert "retry_count" not in type(plan).model_fields


def test_execution_plan_rejects_duplicate_or_unknown_dependencies():
    with pytest.raises(ValidationError, match="task_id values must be unique"):
        ExecutionPlan(
            execution_id="exec-1",
            interview_plan_ref="plan-1",
            task_definitions=(_task("same"), _task("same")),
        )

    with pytest.raises(
        ValidationError,
        match="dependency successor must reference a defined task",
    ):
        ExecutionPlan(
            execution_id="exec-1",
            interview_plan_ref="plan-1",
            task_definitions=(_task("evaluate"),),
            dependency_definitions=(
                ExecutionDependencyDefinition(
                    predecessor_task_id="evaluate",
                    successor_task_id="missing",
                ),
            ),
        )


def test_execution_plan_rejects_dependency_cycles():
    with pytest.raises(ValidationError, match="must be acyclic"):
        ExecutionPlan(
            execution_id="exec-1",
            interview_plan_ref="plan-1",
            task_definitions=(_task("a"), _task("b")),
            dependency_definitions=(
                ExecutionDependencyDefinition(
                    predecessor_task_id="a", successor_task_id="b"
                ),
                ExecutionDependencyDefinition(
                    predecessor_task_id="b", successor_task_id="a"
                ),
            ),
        )


def test_execution_plan_is_pure_domain_without_runtime_or_transport_imports():
    path = ROOT / "app" / "domain" / "interview" / "scheduling" / "plan.py"
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
