from __future__ import annotations

from types import SimpleNamespace

from app.a2a.contracts.evaluation import EvaluationArtifactPayload
from app.a2a.contracts.main_question import MainQuestionArtifactPayload
from app.adapters.memory.execution_path_binding import (
    InMemoryExecutionPathBindingStore,
)
from app.adapters.memory.execution_artifacts import InMemoryExecutionArtifactStore
from app.adapters.memory.scheduler_execution import (
    InMemorySchedulerExecutionRepository,
)
from app.adapters.memory.session_store import InterviewSessionStore
from app.application.interview.orchestration_cutover import ExecutionPathRouter
from app.application.interview.scheduler_production_entry import (
    SchedulerProductionEntry,
    build_interview_execution,
)
from app.application.scheduling import (
    DeterministicSchedulerPolicy,
    InMemoryExecutionStateStore,
    SchedulerApplicationCapability,
)
from app.domain.interview.commands import SessionCommand
from app.domain.interview.prep import InterviewPlan, InterviewQuestion
from app.domain.interview.scheduling import (
    CapabilityDescriptor,
    ExecutionPlan,
    ExecutionState,
    InterviewPlanItemSlice,
    InterviewPlanSlice,
    SchedulerBudget,
    SchedulerContext,
    SchedulerObservation,
    TaskRuntimeState,
    UserCommand,
    WaitHandle,
    derive_adaptive_followup_decision,
    execute_evidence_insufficient_replan,
)
from app.runtime.agent_execution import AgentExecutionContext, AgentExecutionRunner


class _Catalog:
    def __init__(self) -> None:
        self.capabilities = {
            ("interview-examiner", "generate-main-question"): CapabilityDescriptor(
                agent_id="interview-examiner",
                skill="generate-main-question",
                description="Generate a main question.",
                request_contract_id="generate-main-question-request",
                request_contract_version="v1",
                output_artifact_type="main-question-artifact",
                output_artifact_version="1.0",
                capability_version="v1",
            ),
            ("interview-reviewer", "evaluate-answer"): CapabilityDescriptor(
                agent_id="interview-reviewer",
                skill="evaluate-answer",
                description="Evaluate an answer.",
                request_contract_id="evaluate-answer-request",
                request_contract_version="v1",
                output_artifact_type="evaluation-artifact",
                output_artifact_version="1.0",
                capability_version="v1",
            ),
        }

    def resolve(self, *, agent_id, skill, capability_version=None):
        return self.capabilities.get((agent_id, skill))

    def validate_compatibility(self, **kwargs):
        return self.resolve(
            agent_id=kwargs["agent_id"], skill=kwargs["skill"]
        ) is not None


class _Invoker:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def invoke(self, *, agent_id, skill, request, execution_context=None):
        self.calls.append((agent_id, skill))
        if skill == "generate-main-question":
            return MainQuestionArtifactPayload(
                question_id=request.intent["question_id"],
                question_text=request.intent["fixed_question_text"],
                render_mode="fixed",
                reason_code="fixed_plan_question",
            )
        return EvaluationArtifactPayload(
            question_id=request.question_id,
            score=80,
            evaluation_policy_version="baseline-v1",
        )


class _Recorder:
    def __init__(self) -> None:
        self.records = []

    def record(self, record) -> None:
        self.records.append(record)


def _plan(question_count: int = 2) -> InterviewPlan:
    return InterviewPlan(
        title="MA9 baseline",
        questions=[
            InterviewQuestion(
                id=f"q{position}",
                kind="technical",
                prompt=f"Question {position}?",
                focus=f"focus-{position}",
            )
            for position in range(1, question_count + 1)
        ],
    )


def _production_entry():
    session_store = InterviewSessionStore()
    repository = InMemorySchedulerExecutionRepository()
    router = ExecutionPathRouter(InMemoryExecutionPathBindingStore())
    invoker = _Invoker()
    artifact_store = InMemoryExecutionArtifactStore()

    def compose(*, plan, initial_state, execution_state_store, **_kwargs):
        return SimpleNamespace(
            scheduler=SchedulerApplicationCapability(
                state_store=execution_state_store,
                plan=plan,
                capability_port=_Catalog(),
                invocation_port=invoker,
                artifact_store=artifact_store,
                evaluation_state_provider=session_store.get,
            )
        )

    return (
        SchedulerProductionEntry(
            session_store=session_store,
            execution_repository=repository,
            execution_path_router=router,
            scheduler_composer=compose,
            id_generator=lambda: "ma9-baseline",
        ),
        repository,
        invoker,
    )


def test_t02_answer_dispatches_reviewer_without_compatibility_skip():
    entry, repository, invoker = _production_entry()
    turn = entry.start(
        _plan(),
        job_description="backend",
        resume_text="built services",
        job_tags=[],
    )

    entry.execute(
        SessionCommand.answer(
            turn.session_id,
            "I use a bounded queue.",
            command_id="answer-1",
        )
    )

    assert invoker.calls.count(("interview-reviewer", "evaluate-answer")) == 1
    evaluation = repository.load(turn.session_id).task_state("evaluate:1:q1")
    assert evaluation is not None
    assert evaluation.status == "COMPLETED"
    assert evaluation.reason_code is None


def test_t03_unresolved_gate_blocks_next_main_after_evaluation():
    plan, initial = build_interview_execution(
        session_id="early-next-main",
        plan=_plan(),
    )
    state = ExecutionState(
        execution_id=initial.execution_id,
        task_states=tuple(
            TaskRuntimeState(
                task_id=task.task_id,
                status=(
                    "COMPLETED"
                    if task.task_id in {"main:1:q1", "evaluate:1:q1"}
                    else "PENDING"
                ),
            )
            for task in plan.task_definitions
        ),
        latest_observation={
            "task_id": "evaluate:1:q1",
            "status": "COMPLETED",
            "artifact_type": "evaluation-artifact",
        },
        execution_status="RUNNING",
    )

    decision = DeterministicSchedulerPolicy().decide(plan=plan, state=state)

    assert decision.action == "NOOP"
    assert decision.reason_code == "no_valid_next_action"
    assert state.task_state("resolve:1:q1").status == "PENDING"
    assert any(task.task_id == "resolve:1:q1" for task in plan.task_definitions)


def test_t02_answer_is_recoverable_as_durable_artifact():
    wait = WaitHandle(
        wait_id="wait:answer-gap",
        execution_id="answer-gap",
        task_id="main:1:q1",
        question_id="q1",
        issued_revision=1,
    )
    state = ExecutionState(
        execution_id="answer-gap",
        revision=1,
        task_states=(TaskRuntimeState(task_id="main:1:q1", status="COMPLETED"),),
        current_wait_handle=wait,
        execution_status="WAITING",
    )
    store = InMemoryExecutionStateStore(state)
    artifact_store = InMemoryExecutionArtifactStore()
    scheduler = SchedulerApplicationCapability(
        state_store=store,
        plan=ExecutionPlan(execution_id="answer-gap", interview_plan_ref="plan-1"),
        capability_port=SimpleNamespace(),
        invocation_port=SimpleNamespace(),
        artifact_store=artifact_store,
    )

    accepted = scheduler.accept_user_command(
        "answer-gap",
        UserCommand(
            command_id="answer-command-1",
            execution_id="answer-gap",
            wait_id=wait.wait_id,
            task_id=wait.task_id,
            question_id=wait.question_id,
            expected_revision=wait.issued_revision,
            payload={"answer_text": "durable facts matter"},
        ),
    )

    assert accepted.accepted
    assert any(
        ref.artifact_type == "answer-artifact" for ref in accepted.state.artifact_refs
    )
    assert accepted.state.latest_observation == {
        "status": "ANSWER_RECEIVED",
        "task_id": "main:1:q1",
        "question_id": "q1",
        "command_id": "answer-command-1",
        "answer_artifact_ref": accepted.state.artifact_refs[-1].artifact_ref,
        "wait_id": "wait:answer-gap",
        "submitted_revision": 1,
    }
    artifact = artifact_store.get_required(
        accepted.state.artifact_refs[-1].artifact_ref
    )
    assert artifact.answer_text == "durable facts matter"


def test_t04_dynamic_replan_creates_followup_evaluation_pair():
    review_task = TaskRuntimeState(task_id="evaluate:1:q1", status="COMPLETED")
    context = SchedulerContext(
        interview_plan_slice=InterviewPlanSlice(
            plan_ref="plan-1",
            plan_revision=1,
            current_question_id="q1",
            items=(
                InterviewPlanItemSlice(
                    question_id="q1",
                    position=1,
                    question_type="technical",
                    focus="queue bounds",
                ),
            ),
        ),
        execution_state=ExecutionState(
            execution_id="dynamic-gap",
            task_states=(review_task,),
            latest_observation={
                "task_id": review_task.task_id,
                "status": "INSUFFICIENT_EVIDENCE",
                "question_id": "q1",
            },
            execution_status="RUNNING",
        ),
        ready_tasks=(),
        recent_observations=(
            SchedulerObservation(
                observation_ref="observation:evaluate:1:q1",
                task_id=review_task.task_id,
                status="INSUFFICIENT_EVIDENCE",
                summary="The answer lacks queue overflow handling.",
            ),
        ),
        capabilities=(
                CapabilityDescriptor(
                agent_id="interview-examiner",
                skill="generate-followup",
                description="Generate a follow-up.",
                request_contract_id="generate-followup-request",
                request_contract_version="v1",
                output_artifact_type="followup-artifact",
                output_artifact_version="1.0",
                    capability_version="v1",
                ),
                CapabilityDescriptor(
                    agent_id="interview-reviewer",
                    skill="evaluate-answer",
                    description="Evaluate a follow-up answer.",
                    request_contract_id="evaluate-answer-request",
                    request_contract_version="v1",
                    output_artifact_type="evaluation-artifact",
                    output_artifact_version="evaluation-artifact-v2",
                    capability_version="v1",
                ),
            ),
        budget=SchedulerBudget(
            max_scheduler_steps=10,
            remaining_scheduler_steps=8,
            max_tasks=6,
            remaining_task_slots=4,
            max_replans=2,
            remaining_replans=2,
            max_followups=2,
            remaining_followups=2,
        ),
    )

    decision = derive_adaptive_followup_decision(context)
    replanned, followup, _request = execute_evidence_insufficient_replan(
        context, decision.add_task
    )

    assert len(replanned.dynamic_task_definitions) == 2
    assert replanned.dynamic_task_definitions[0] == followup
    assert followup.skill == "generate-followup"
    assert replanned.dynamic_task_definitions[1].skill == "evaluate-answer"
    assert replanned.followups_total_used == 1
    assert replanned.replans_used == 1


def test_current_stream_disconnect_cancels_or_terminates_generation():
    recorder = _Recorder()
    runner = AgentExecutionRunner(recorder=recorder)
    stream = runner.stream(
        AgentExecutionContext(
            correlation_id="ma9-stream",
            agent="examiner",
            operation="generate_question",
            phase="interview",
            session_id="stream-gap",
        ),
        lambda: iter(("first", "second")),
    )

    assert next(stream) == "first"
    stream.close()

    assert recorder.records[0].status == "cancelled"
    assert recorder.records[0].fallback_reason == "client_disconnected"
