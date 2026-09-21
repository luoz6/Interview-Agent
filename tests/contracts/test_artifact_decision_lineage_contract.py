from __future__ import annotations

import hashlib
import json

from app.adapters.memory.agent_invocation_ledger import (
    InMemoryAgentInvocationLedger,
)
from app.application.interview.scheduler_production_entry import (
    build_interview_execution,
)
from app.application.scheduling import (
    InMemoryExecutionStateStore,
    SchedulerApplicationCapability,
)
from app.domain.agents import DomainArtifact
from app.domain.interview.prep import InterviewPlan, InterviewQuestion
from app.domain.interview.scheduling import (
    CapabilityDescriptor,
    ExecutionArtifactRef,
    ExecutionState,
    InterviewPlanItemSlice,
    InterviewPlanSlice,
    InvocationIdentity,
    SchedulerBudget,
    SchedulerContext,
    SchedulerObservation,
    TaskRuntimeState,
    derive_adaptive_followup_decision,
)


class CapabilityCatalog:
    evaluation = CapabilityDescriptor(
        agent_id="interview-reviewer",
        skill="evaluate-answer",
        description="Evaluate one answer.",
        request_contract_id="evaluate-answer-request",
        request_contract_version="v1",
        output_artifact_type="evaluation-artifact",
        output_artifact_version="1.0",
        capability_version="v1",
    )

    def resolve(self, *, agent_id, skill, capability_version=None):
        if (agent_id, skill) == (
            self.evaluation.agent_id,
            self.evaluation.skill,
        ):
            return self.evaluation
        return None

    def validate_compatibility(self, **kwargs):
        return self.resolve(
            agent_id=kwargs["agent_id"],
            skill=kwargs["skill"],
        ) is not None


class EvaluationInvoker:
    def invoke(self, *, agent_id, skill, request, execution_context=None):
        return DomainArtifact(
            artifact_type="evaluation-artifact",
            context_ref={"evidence_status": "INSUFFICIENT_EVIDENCE"},
        )


def _interview_plan() -> InterviewPlan:
    return InterviewPlan(
        title="Lineage interview",
        questions=[
            InterviewQuestion(
                id="q1",
                kind="technical",
                prompt="Explain consistency tradeoffs.",
                focus="consistency tradeoffs",
            )
        ],
    )


def _followup_capability() -> CapabilityDescriptor:
    return CapabilityDescriptor(
        agent_id="interview-examiner",
        skill="generate-followup",
        description="Acquire missing evidence.",
        request_contract_id="generate-followup-request",
        request_contract_version="v1",
        output_artifact_type="followup-artifact",
        output_artifact_version="1.0",
        capability_version="v1",
    )


def test_interview_plan_to_scheduling_decision_lineage_is_complete() -> None:
    interview_plan = _interview_plan()
    execution_plan, initial_state = build_interview_execution(
        session_id="lineage-execution",
        plan=interview_plan,
    )
    plan_payload = interview_plan.model_dump(mode="json")
    plan_digest = hashlib.sha256(
        json.dumps(
            plan_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    main_task = execution_plan.task_definitions[0]
    evaluation_task = execution_plan.task_definitions[1]
    answer_ref = ExecutionArtifactRef(
        artifact_ref="lineage-execution/answer/q1",
        artifact_type="answer-artifact",
        task_id=main_task.task_id,
    )
    source_state = ExecutionState(
        execution_id=execution_plan.execution_id,
        revision=7,
        task_states=tuple(
            TaskRuntimeState(
                task_id=task.task_id,
                status=(
                    "COMPLETED"
                    if task.task_id == main_task.task_id
                    else (
                        "READY"
                        if task.task_id == evaluation_task.task_id
                        else "PENDING"
                    )
                ),
            )
            for task in execution_plan.task_definitions
        ),
        artifact_refs=initial_state.artifact_refs + (answer_ref,),
        latest_observation={
            "task_id": main_task.task_id,
            "status": "ANSWER_RECEIVED",
            "artifact_ref": answer_ref.artifact_ref,
        },
        execution_status="RUNNING",
    )
    state_store = InMemoryExecutionStateStore(source_state)
    ledger = InMemoryAgentInvocationLedger()
    scheduler = SchedulerApplicationCapability(
        state_store=state_store,
        plan=execution_plan,
        capability_port=CapabilityCatalog(),
        invocation_port=EvaluationInvoker(),
        invocation_ledger=ledger,
        worker_id="lineage-worker",
    )

    dispatched = scheduler.step(execution_plan.execution_id)
    runtime_task = dispatched.state.task_state(evaluation_task.task_id)
    identity = InvocationIdentity(
        execution_id=execution_plan.execution_id,
        task_id=evaluation_task.task_id,
        logical_attempt=runtime_task.attempt,
    )
    invocation = ledger.get(identity)
    observation = dispatched.observation

    assert execution_plan.interview_plan_ref == f"sha256:{plan_digest}"
    assert initial_state.artifact_refs[0].artifact_ref.endswith(plan_digest[:16])
    assert source_state.execution_id == execution_plan.execution_id
    assert dispatched.state.revision > source_state.revision
    assert evaluation_task in execution_plan.task_definitions
    assert invocation.identity == identity
    assert invocation.status == "COMPLETED"
    assert dispatched.artifact.artifact_type == "evaluation-artifact"
    assert invocation.artifact_ref == observation["artifact_ref"]
    assert dispatched.state.artifact_refs[-1].artifact_ref == invocation.artifact_ref
    assert observation["interview_plan_ref"] == execution_plan.interview_plan_ref
    assert observation["execution_plan_revision"] == execution_plan.revision
    assert observation["execution_id"] == dispatched.state.execution_id
    assert observation["execution_state_revision"] == dispatched.state.revision
    assert observation["task_id"] == evaluation_task.task_id
    assert observation["logical_invocation"] == identity.model_dump(mode="json")
    assert observation["observation_ref"] == (
        f"{invocation.artifact_ref}/observation"
    )

    bounded_observation = SchedulerObservation(
        observation_ref=observation["observation_ref"],
        task_id=observation["task_id"],
        status="INSUFFICIENT_EVIDENCE",
        summary="The evaluation requires a focused consistency example.",
        artifact_refs=(observation["artifact_ref"],),
    )
    context = SchedulerContext(
        interview_plan_slice=InterviewPlanSlice(
            plan_ref=observation["interview_plan_ref"],
            plan_revision=observation["execution_plan_revision"],
            current_question_id="q1",
            items=(
                InterviewPlanItemSlice(
                    question_id="q1",
                    position=1,
                    question_type="technical",
                    focus="consistency tradeoffs",
                ),
            ),
        ),
        execution_state=dispatched.state,
        ready_tasks=(),
        recent_observations=(bounded_observation,),
        capabilities=(_followup_capability(),),
        budget=SchedulerBudget(
            max_scheduler_steps=20,
            remaining_scheduler_steps=10,
            max_tasks=8,
            remaining_task_slots=2,
        ),
    )
    decision = derive_adaptive_followup_decision(context)

    assert context.execution_state.revision == observation["execution_state_revision"]
    assert context.recent_observations[0].artifact_refs == (
        invocation.artifact_ref,
    )
    assert decision.action == "ADD_TASK"
    assert decision.reason_code == "evidence_insufficient"
    assert decision.add_task.dependencies == (evaluation_task.task_id,)
