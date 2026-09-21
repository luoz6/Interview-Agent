from __future__ import annotations

from app.a2a.runtime import build_local_a2a_runtime
from app.adapters.memory.agent_invocation_ledger import (
    InMemoryAgentInvocationLedger,
)
from app.application.scheduling import (
    InMemoryExecutionStateStore,
    SchedulerApplicationCapability,
)
from app.domain.interview.scheduling import (
    ExecutionPlan,
    ExecutionState,
    ExecutionTaskDefinition,
    InterviewPlanItemSlice,
    InterviewPlanSlice,
    InvocationIdentity,
    SchedulerBudget,
    SchedulerContext,
    SchedulerObservation,
    TaskRuntimeState,
    UserCommand,
    derive_adaptive_followup_decision,
    execute_evidence_insufficient_replan,
)


class AdaptiveLLM:
    def generate_followup(self, _context):
        return "Which consistency tradeoff would you choose, and why?"


def test_insufficient_evidence_replans_dispatches_and_completes_adaptive_interview():
    review_task = ExecutionTaskDefinition(
        task_id="review-answer",
        capability="interview.answer-evaluation",
        agent_id="interview-reviewer",
        skill="evaluate-answer",
        input_contract="evaluate-answer-request",
        output_contract="evaluation-artifact",
        parameters={"question_id": "q1"},
    )
    plan = ExecutionPlan(
        execution_id="adaptive-e2e",
        interview_plan_ref="adaptive-plan-v1",
        task_definitions=(review_task,),
    )
    state = ExecutionState(
        execution_id=plan.execution_id,
        task_states=(
            TaskRuntimeState(task_id=review_task.task_id, status="COMPLETED"),
        ),
        latest_observation={
            "task_id": review_task.task_id,
            "status": "INSUFFICIENT_EVIDENCE",
            "question_id": "q1",
            "focus": "consistency tradeoffs",
        },
        execution_status="RUNNING",
    )
    a2a = build_local_a2a_runtime(llm=AdaptiveLLM())
    context = SchedulerContext(
        interview_plan_slice=InterviewPlanSlice(
            plan_ref=plan.interview_plan_ref,
            plan_revision=plan.definition_revision,
            current_question_id="q1",
            items=(
                InterviewPlanItemSlice(
                    question_id="q1",
                    position=1,
                    question_type="system-design",
                    focus="consistency tradeoffs",
                    expected_followups=1,
                ),
            ),
        ),
        execution_state=state,
        ready_tasks=(),
        recent_observations=(
            SchedulerObservation(
                observation_ref="observation:review-answer:1",
                task_id=review_task.task_id,
                status="INSUFFICIENT_EVIDENCE",
                summary="The answer did not establish a consistency tradeoff.",
            ),
        ),
        capabilities=a2a.registry.list_capabilities(),
        budget=SchedulerBudget(
            max_scheduler_steps=10,
            remaining_scheduler_steps=8,
            max_tasks=4,
            remaining_task_slots=2,
            max_agent_calls=3,
            remaining_agent_calls=3,
            max_replans=1,
            remaining_replans=1,
            max_followups=1,
            remaining_followups=1,
        ),
    )

    decision = derive_adaptive_followup_decision(context)
    replanned, dynamic_task, assembled_request = (
        execute_evidence_insufficient_replan(context, decision.add_task)
    )
    state_store = InMemoryExecutionStateStore(replanned)
    ledger = InMemoryAgentInvocationLedger()
    scheduler = SchedulerApplicationCapability(
        state_store=state_store,
        plan=plan,
        capability_port=a2a.registry,
        invocation_port=a2a.invoker,
        invocation_ledger=ledger,
        worker_id="adaptive-e2e-worker",
    )

    dispatched = scheduler.step(plan.execution_id)
    waiting = scheduler.step(plan.execution_id)
    wait = waiting.state.current_wait_handle
    accepted = scheduler.accept_user_command(
        plan.execution_id,
        UserCommand(
            command_id="adaptive-answer-1",
            execution_id=plan.execution_id,
            wait_id=wait.wait_id,
            task_id=wait.task_id,
            question_id=wait.question_id,
            expected_revision=wait.issued_revision,
            payload={
                "answer_text": "I would choose bounded staleness and monitor lag."
            },
        ),
    )
    completed = scheduler.step(plan.execution_id)
    invocation = ledger.get(
        InvocationIdentity(
            execution_id=plan.execution_id,
            task_id=dynamic_task.task_id,
            logical_attempt=1,
        )
    )

    assert decision.action == "ADD_TASK"
    assert decision.reason_code == "evidence_insufficient"
    assert dynamic_task in replanned.dynamic_task_definitions
    assert type(assembled_request).__name__ == "GenerateFollowupRequest"
    assert dispatched.action == "DISPATCH"
    assert dispatched.task == dynamic_task
    assert type(dispatched.request).__name__ == "GenerateFollowupRequest"
    assert dispatched.request.question_id == "q1"
    assert dispatched.artifact.artifact_type == "followup-artifact"
    assert waiting.action == "WAIT_USER"
    assert wait.task_id == dynamic_task.task_id
    assert wait.question_id == "q1"
    assert accepted.accepted
    assert completed.action == "COMPLETE"
    assert completed.state.execution_status == "COMPLETED"
    assert completed.state.current_wait_handle is None
    assert completed.state.task_state(dynamic_task.task_id).status == "COMPLETED"
    assert invocation.status == "COMPLETED"
    assert invocation.artifact_ref == dispatched.observation["artifact_ref"]
    assert [item["skill"] for item in a2a.observability.snapshot()] == [
        "generate-followup"
    ]
