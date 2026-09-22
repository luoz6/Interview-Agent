import ast
from pathlib import Path

import pytest

from app.domain.interview.scheduling import (
    CapabilityDescriptor,
    DynamicTaskDeclaration,
    ExecutionState,
    InterviewPlanSlice,
    SchedulerBudget,
    SchedulerContext,
    SchedulerObservation,
    SchedulingDecision,
    SchedulingDecisionValidationError,
    TaskRuntimeState,
    build_evidence_insufficient_replan,
    execute_evidence_insufficient_replan,
    is_evidence_insufficient,
    validate_evidence_insufficient_replan,
)


ROOT = Path(__file__).resolve().parents[2]


def _context(*, status="INSUFFICIENT_EVIDENCE", latest_status=None) -> SchedulerContext:
    latest_observation = {
        "task_id": "review-answer",
        "status": latest_status or status,
        "question_id": "q1",
        "reason": "missing consistency evidence",
    }
    return SchedulerContext(
        interview_plan_slice=InterviewPlanSlice(plan_ref="plan-1", plan_revision=1),
        execution_state=ExecutionState(
            execution_id="exec-1",
            task_states=(
                TaskRuntimeState(task_id="review-answer", status="COMPLETED"),
            ),
            latest_observation=latest_observation,
            execution_status="RUNNING",
        ),
        ready_tasks=(),
        recent_observations=(
            SchedulerObservation(
                observation_ref="observation:review:1",
                task_id="review-answer",
                status=status,
                summary="The answer lacks evidence for consistency tradeoffs.",
            ),
        ),
        capabilities=(
            CapabilityDescriptor(
                agent_id="interview-examiner",
                skill="generate-followup",
                description="Acquire a focused follow-up answer.",
                request_contract_id="generate-followup-request",
                request_contract_version="v1",
                output_artifact_type="followup-artifact",
                output_artifact_version="1.0",
                capability_version="v1",
            ),
            CapabilityDescriptor(
                agent_id="interview-reviewer",
                skill="evaluate-answer",
                description="Review the acquired answer.",
                request_contract_id="evaluate-answer-request",
                request_contract_version="v1",
                output_artifact_type="evaluation-artifact",
                output_artifact_version="1.0",
                capability_version="v1",
            ),
        ),
        budget=SchedulerBudget(
            max_scheduler_steps=10,
            remaining_scheduler_steps=4,
            max_tasks=10,
            remaining_task_slots=2,
        ),
    )


def _declaration() -> DynamicTaskDeclaration:
    return DynamicTaskDeclaration(
        capability="generate-followup",
        reason="acquire missing consistency evidence",
        dependencies=("review-answer",),
    )


def test_reviewer_insufficient_evidence_is_a_bounded_replan_trigger():
    context = _context()
    assert is_evidence_insufficient(context)
    decision = build_evidence_insufficient_replan(context, _declaration())
    assert decision.action == "ADD_TASK"
    assert decision.reason_code == "evidence_insufficient"
    assert decision.add_task is not None


def test_replan_acquires_followup_then_uses_typed_request_and_registers_task():
    state, task, request = execute_evidence_insufficient_replan(
        _context(), _declaration()
    )
    assert task.skill == "generate-followup"
    assert type(request).__name__ == "GenerateFollowupRequest"
    assert request.question_id == "q1"
    assert state.task_state(task.task_id).status == "PENDING"
    assert [item.task_id for item in state.dynamic_task_definitions] == [
        "followup:q1:1",
        "evaluate-followup:q1:1",
    ]
    assert state.followups_total_used == state.replans_used == 1


def test_replan_rejects_non_insufficient_observations_and_non_acquisition_actions():
    context = _context(status="SUFFICIENT", latest_status="SUFFICIENT")
    assert not is_evidence_insufficient(context)
    decision = SchedulingDecision(action="COMPLETE", reason_code="complete")
    with pytest.raises(SchedulingDecisionValidationError) as not_triggered:
        validate_evidence_insufficient_replan(context, decision)
    assert not_triggered.value.code == "replan_not_triggered"

    context = _context()
    bad_decision = SchedulingDecision(
        action="ADD_TASK",
        reason_code="evidence_insufficient",
        add_task=DynamicTaskDeclaration(
            capability="evaluate-answer",
            reason="review again without acquiring evidence",
            dependencies=("review-answer",),
        ),
    )
    with pytest.raises(SchedulingDecisionValidationError) as bad_capability:
        validate_evidence_insufficient_replan(context, bad_decision)
    assert bad_capability.value.code == "replan_capability_invalid"


def test_replan_requires_dependency_on_the_insufficient_reviewer_task():
    with pytest.raises(SchedulingDecisionValidationError) as missing_dependency:
        build_evidence_insufficient_replan(
            _context(),
            DynamicTaskDeclaration(
                capability="generate-followup",
                reason="acquire missing evidence",
            ),
        )
    assert missing_dependency.value.code == "replan_dependency_missing"


def test_replan_contract_is_domain_only():
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
