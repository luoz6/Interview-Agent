import ast
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.domain.interview.scheduling import (
    CapabilityDescriptor,
    DynamicTaskDeclaration,
    ExecutionArtifactRef,
    ExecutionState,
    InterviewPlanSlice,
    SchedulerArtifactSummary,
    SchedulerBudget,
    SchedulerContext,
    SchedulingDecision,
    SchedulingDecisionValidationError,
    TaskRuntimeState,
    assemble_dynamic_task_request,
    materialize_dynamic_task,
    register_dynamic_task,
    validate_scheduling_decision,
)


ROOT = Path(__file__).resolve().parents[2]


def _capability() -> CapabilityDescriptor:
    return CapabilityDescriptor(
        agent_id="interview-reviewer",
        skill="evaluate-answer",
        description="Evaluate one answer.",
        request_contract_id="evaluate-answer-request",
        request_contract_version="v1",
        output_artifact_type="evaluation-artifact",
        output_artifact_version="1.0",
        required_input_artifact_types=("answer-artifact",),
        capability_version="v1",
    )


def _context() -> SchedulerContext:
    artifact = ExecutionArtifactRef(
        artifact_ref="artifact:answer:1",
        artifact_type="answer-artifact",
        task_id="answer",
    )
    return SchedulerContext(
        interview_plan_slice=InterviewPlanSlice(plan_ref="plan-1", plan_revision=1),
        execution_state=ExecutionState(
            execution_id="exec-1",
            task_states=(TaskRuntimeState(task_id="answer", status="COMPLETED"),),
            artifact_refs=(artifact,),
            latest_observation={"answer": "I reduced latency."},
        ),
        ready_tasks=(),
        artifact_summaries=(
            SchedulerArtifactSummary(
                artifact_ref=artifact.artifact_ref,
                artifact_type=artifact.artifact_type,
                task_id=artifact.task_id,
                summary="Candidate answer artifact.",
            ),
        ),
        capabilities=(_capability(),),
        budget=SchedulerBudget(
            max_scheduler_steps=10,
            remaining_scheduler_steps=4,
            max_tasks=8,
            remaining_task_slots=2,
        ),
    )


def _declaration(**changes) -> DynamicTaskDeclaration:
    values = {
        "capability": "evaluate-answer",
        "required_inputs": ("answer-artifact",),
        "artifact_refs": ("artifact:answer:1",),
        "reason": "evidence is needed",
        "dependencies": ("answer",),
    }
    values.update(changes)
    return DynamicTaskDeclaration(**values)


def test_add_task_is_a_closed_declaration_without_request_dict():
    declaration = _declaration()
    decision = SchedulingDecision(
        action="ADD_TASK",
        reason_code="need_evidence",
        add_task=declaration,
    )
    assert decision.add_task == declaration
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        DynamicTaskDeclaration(**declaration.model_dump(), request={"state": {}})
    with pytest.raises(ValidationError, match="ADD_TASK requires add_task"):
        SchedulingDecision(action="ADD_TASK", reason_code="missing_declaration")


def test_add_task_validates_capability_inputs_artifacts_and_dependencies():
    context = _context()
    decision = SchedulingDecision(
        action="ADD_TASK",
        reason_code="need_evidence",
        add_task=_declaration(),
    )
    assert validate_scheduling_decision(context, decision) is decision

    with pytest.raises(SchedulingDecisionValidationError) as missing_input:
        validate_scheduling_decision(
            context,
            decision.model_copy(update={"add_task": _declaration(required_inputs=())}),
        )
    assert missing_input.value.code == "required_input_missing"

    with pytest.raises(SchedulingDecisionValidationError) as missing_ref:
        validate_scheduling_decision(
            context,
            decision.model_copy(
                update={"add_task": _declaration(artifact_refs=("missing",))}
            ),
        )
    assert missing_ref.value.code == "artifact_ref_missing"

    with pytest.raises(SchedulingDecisionValidationError) as unknown_dependency:
        validate_scheduling_decision(
            context,
            decision.model_copy(
                update={"add_task": _declaration(dependencies=("missing",))}
            ),
        )
    assert unknown_dependency.value.code == "dependency_unknown"


def test_add_task_materialization_is_deterministic_and_assembler_returns_typed_request():
    context = _context()
    declaration = _declaration()
    first = materialize_dynamic_task(context, declaration)
    second = materialize_dynamic_task(context, declaration)
    assert first == second
    assert first.task_id.startswith("dynamic-")
    assert first.parameters["artifact_refs"] == declaration.artifact_refs
    assert first.parameters["dependencies"] == declaration.dependencies

    task, request = assemble_dynamic_task_request(context, declaration)
    assert task == first
    assert type(request).__name__ == "EvaluateAnswerRequest"
    assert request.state["latest_observation"]["answer"] == "I reduced latency."


def test_dynamic_task_registration_uses_canonical_state_transition():
    context = _context()
    task = materialize_dynamic_task(context, _declaration())
    state = register_dynamic_task(context.execution_state, task)
    assert state.revision == context.execution_state.revision + 1
    assert state.task_state(task.task_id).status == "PENDING"
    assert state.dynamic_task_definitions == (task,)
    with pytest.raises(SchedulingDecisionValidationError, match="already exists"):
        register_dynamic_task(state, task)


def test_dynamic_task_contract_is_domain_only_and_does_not_import_adapters():
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
