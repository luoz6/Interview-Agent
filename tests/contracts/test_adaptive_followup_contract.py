import ast
from pathlib import Path

import pytest

from app.domain.interview.scheduling import (
    CapabilityDescriptor,
    ExecutionState,
    InterviewPlanItemSlice,
    InterviewPlanSlice,
    SchedulerBudget,
    SchedulerContext,
    SchedulerObservation,
    SchedulerReadyTask,
    SchedulingDecisionValidationError,
    TaskRuntimeState,
    derive_adaptive_followup_decision,
    validate_scheduling_decision,
)


ROOT = Path(__file__).resolve().parents[2]


def _plan() -> InterviewPlanSlice:
    return InterviewPlanSlice(
        plan_ref="plan-constant",
        plan_revision=4,
        current_question_id="q1",
        items=(
            InterviewPlanItemSlice(
                question_id="q1",
                position=1,
                question_type="system-design",
                focus="Consistency tradeoffs",
                intent_summary="Assess distributed systems reasoning.",
            ),
        ),
    )


def _followup_capability() -> CapabilityDescriptor:
    return CapabilityDescriptor(
        agent_id="interview-examiner",
        skill="generate-followup",
        description="Generate a focused follow-up.",
        request_contract_id="generate-followup-request",
        request_contract_version="v1",
        output_artifact_type="followup-artifact",
        output_artifact_version="1.0",
        capability_version="v1",
    )


def _review_capability() -> CapabilityDescriptor:
    return CapabilityDescriptor(
        agent_id="interview-reviewer",
        skill="evaluate-answer",
        description="Review the candidate answer.",
        request_contract_id="evaluate-answer-request",
        request_contract_version="v1",
        output_artifact_type="evaluation-artifact",
        output_artifact_version="1.0",
        capability_version="v1",
    )


def _context(*, observation_status: str, ready: bool) -> SchedulerContext:
    task_states = [TaskRuntimeState(task_id="review-answer", status="COMPLETED")]
    ready_tasks = ()
    capabilities = [_followup_capability()]
    if ready:
        reviewer = SchedulerReadyTask(
            task_id="review-followup",
            capability="interview.evaluation",
            agent_id="interview-reviewer",
            skill="evaluate-answer",
            input_contract="evaluate-answer-request",
            output_contract="evaluation-artifact",
            attempt=0,
            max_attempts=1,
        )
        ready_tasks = (reviewer,)
        task_states.append(
            TaskRuntimeState(task_id="review-followup", status="READY")
        )
        capabilities.append(_review_capability())
    return SchedulerContext(
        interview_plan_slice=_plan(),
        execution_state=ExecutionState(
            execution_id="exec-1",
            task_states=tuple(task_states),
            latest_observation={
                "task_id": "review-answer",
                "status": observation_status,
                "question_id": "q1",
            },
            execution_status="RUNNING",
        ),
        ready_tasks=ready_tasks,
        recent_observations=(
            SchedulerObservation(
                observation_ref=f"observation:{observation_status}",
                task_id="review-answer",
                status=observation_status,
                summary="bounded candidate observation",
            ),
        ),
        capabilities=tuple(capabilities),
        budget=SchedulerBudget(
            max_scheduler_steps=10,
            remaining_scheduler_steps=6,
            max_tasks=10,
            remaining_task_slots=3,
        ),
    )


def test_same_interview_plan_maps_distinct_candidate_observations_to_legal_decisions():
    insufficient = _context(
        observation_status="INSUFFICIENT_EVIDENCE", ready=False
    )
    sufficient = _context(observation_status="SUFFICIENT_EVIDENCE", ready=True)

    assert insufficient.interview_plan_slice == sufficient.interview_plan_slice
    decision_a = derive_adaptive_followup_decision(insufficient)
    decision_b = derive_adaptive_followup_decision(sufficient)

    assert decision_a.action == "ADD_TASK"
    assert decision_a.add_task is not None
    assert decision_a.add_task.capability == "generate-followup"
    assert decision_b.action == "DISPATCH"
    assert decision_b.task_id == "review-followup"
    assert decision_a != decision_b
    assert validate_scheduling_decision(insufficient, decision_a) is decision_a
    assert validate_scheduling_decision(sufficient, decision_b) is decision_b


def test_adaptive_followup_does_not_accept_unbounded_or_unrecognized_observations():
    with pytest.raises(SchedulingDecisionValidationError) as unknown:
        derive_adaptive_followup_decision(
            _context(observation_status="MODEL_GUESS", ready=False)
        )
    assert unknown.value.code == "wait_not_required"


def test_adaptive_followup_contract_is_domain_only():
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
