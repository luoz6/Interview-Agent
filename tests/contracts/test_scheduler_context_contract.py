import ast
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.domain.interview.scheduling import (
    CapabilityDescriptor,
    ExecutionState,
    InterviewPlanItemSlice,
    InterviewPlanSlice,
    SchedulerArtifactSummary,
    SchedulerBudget,
    SchedulerContext,
    SchedulerMemory,
    SchedulerObservation,
    SchedulerReadyTask,
    TaskRuntimeState,
)


ROOT = Path(__file__).resolve().parents[2]


def _task(task_id: str = "review-answer") -> SchedulerReadyTask:
    return SchedulerReadyTask(
        task_id=task_id,
        capability="interview.evaluation",
        agent_id="interview-reviewer",
        skill="evaluate-answer",
        input_contract="evaluate-answer-request",
        output_contract="evaluation-artifact",
        attempt=0,
        max_attempts=1,
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


def _context(**changes) -> SchedulerContext:
    task = _task()
    values = {
        "interview_plan_slice": InterviewPlanSlice(
            plan_ref="plan-revision-7",
            plan_revision=7,
            title="Backend interview",
            current_question_id="q1",
            items=(
                InterviewPlanItemSlice(
                    question_id="q1",
                    position=1,
                    question_type="system-design",
                    focus="Cache invalidation tradeoffs",
                    intent_summary="Assess consistency reasoning.",
                    expected_followups=1,
                ),
            ),
        ),
        "execution_state": ExecutionState(
            execution_id="exec-1",
            task_states=(TaskRuntimeState(task_id=task.task_id, status="READY"),),
            execution_status="RUNNING",
        ),
        "ready_tasks": (task,),
        "recent_observations": (
            SchedulerObservation(
                observation_ref="observation:answer:1",
                task_id="collect-answer",
                status="COMPLETED",
                summary="Candidate compared write-through and write-back caching.",
                artifact_refs=("artifact:answer:1",),
            ),
        ),
        "artifact_summaries": (
            SchedulerArtifactSummary(
                artifact_ref="artifact:answer:1",
                artifact_type="answer-artifact",
                task_id="collect-answer",
                summary="Answer discusses consistency and latency.",
            ),
        ),
        "capabilities": (_capability(),),
        "budget": SchedulerBudget(
            max_scheduler_steps=20,
            remaining_scheduler_steps=12,
            max_tasks=10,
            remaining_task_slots=4,
            max_concurrency=1,
            remaining_model_input_tokens=8_000,
            remaining_model_output_tokens=1_000,
        ),
        "scheduler_memory": SchedulerMemory(
            summary="Prefer evidence-backed follow-ups.",
            decision_refs=("decision:3",),
            observation_refs=("observation:answer:1",),
        ),
    }
    values.update(changes)
    return SchedulerContext(**values)


def test_context_contains_exactly_the_allowed_bounded_inputs():
    context = _context()

    assert set(SchedulerContext.model_fields) == {
        "interview_plan_slice",
        "execution_state",
        "ready_tasks",
        "recent_observations",
        "artifact_summaries",
        "capabilities",
        "budget",
        "scheduler_memory",
    }
    assert context.ready_tasks[0].task_id == "review-answer"
    assert context.scheduler_memory is not None
    assert context.scheduler_memory.decision_refs == ("decision:3",)


def test_context_rejects_database_and_full_agent_memory_payloads():
    payload = _context().model_dump(mode="python")
    payload["database"] = {"sessions": ["all"]}
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        SchedulerContext.model_validate(payload)

    payload = _context().model_dump(mode="python")
    payload["agent_memory"] = {"interview-reviewer": {"all_messages": ["secret"]}}
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        SchedulerContext.model_validate(payload)


def test_context_requires_ready_tasks_to_match_canonical_execution_state():
    task = _task()
    state = ExecutionState(
        execution_id="exec-1",
        task_states=(TaskRuntimeState(task_id=task.task_id, status="PENDING"),),
    )

    with pytest.raises(
        ValidationError,
        match="ready tasks must reference READY ExecutionState tasks",
    ):
        _context(execution_state=state)


def test_context_is_immutable_and_rejects_unbounded_nested_payloads():
    context = _context(scheduler_memory=None)
    assert context.scheduler_memory is None
    with pytest.raises(ValidationError):
        context.scheduler_memory = SchedulerMemory(summary="late mutation")

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        SchedulerObservation(
            observation_ref="observation:1",
            task_id="task-1",
            status="COMPLETED",
            summary="bounded",
            raw_agent_memory={"messages": ["all"]},
        )

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        SchedulerReadyTask(
            **_task().model_dump(mode="python"),
            parameters={"database": {"sessions": ["all"]}},
        )


def test_context_rejects_inconsistent_budgets_and_duplicate_projections():
    with pytest.raises(
        ValidationError, match="remaining scheduler steps cannot exceed"
    ):
        SchedulerBudget(max_scheduler_steps=2, remaining_scheduler_steps=3)

    observation = _context().recent_observations[0]
    with pytest.raises(ValidationError, match="observation_ref values must be unique"):
        _context(recent_observations=(observation, observation))


def test_scheduler_context_is_pure_domain_without_infrastructure_imports():
    path = ROOT / "app" / "domain" / "interview" / "scheduling" / "context.py"
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
    assert all(
        not module.startswith(("openai", "anthropic"))
        for module in imported_modules
    )
