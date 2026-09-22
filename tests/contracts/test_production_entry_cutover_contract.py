from __future__ import annotations

from types import SimpleNamespace

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
from app.application.interview.session_commands import InterviewApplicationService
from app.application.scheduling import SchedulerApplicationCapability
from app.domain.interview.prep import InterviewPlan, InterviewQuestion
from app.domain.interview.commands import SessionCommand
from app.domain.interview.scheduling import CapabilityDescriptor
from app.ports import SchedulerExecutionRepository


class Catalog:
    capability = CapabilityDescriptor(
        agent_id="interview-examiner",
        skill="generate-main-question",
        description="main question",
        request_contract_id="generate-main-question-request",
        request_contract_version="v1",
        output_artifact_type="main-question-artifact",
        output_artifact_version="1.0",
        capability_version="v1",
    )

    def resolve(self, *, agent_id, skill, capability_version=None):
        if (agent_id, skill) == ("interview-examiner", "generate-main-question"):
            return self.capability
        return None

    def validate_compatibility(self, **kwargs):
        return self.resolve(
            agent_id=kwargs["agent_id"], skill=kwargs["skill"]
        ) is not None


class Invoker:
    def __init__(self):
        self.calls = []

    def invoke(self, *, agent_id, skill, request, execution_context=None):
        self.calls.append((agent_id, skill, request))
        return MainQuestionArtifactPayload(
            question_id=request.intent["question_id"],
            question_text=request.intent["fixed_question_text"],
            render_mode="fixed",
            reason_code="fixed_plan_question",
        )


class WorkflowMustNotRun:
    def snapshot(self, session_id):
        raise AssertionError("OLD workflow must not serve a NEW execution")


def _plan():
    return InterviewPlan(
        title="Cutover",
        questions=[
            InterviewQuestion(
                id="q1",
                kind="technical",
                prompt="Explain bounded queues.",
                focus="bounded queues",
            )
        ],
    )


def _runtime():
    store = InterviewSessionStore()
    repository = InMemorySchedulerExecutionRepository()
    bindings = InMemoryExecutionPathBindingStore()
    router = ExecutionPathRouter(bindings)
    invoker = Invoker()
    artifacts = InMemoryExecutionArtifactStore()

    def compose(*, plan, initial_state, execution_state_store, **_kwargs):
        router.claim_execution(initial_state.execution_id, "NEW")
        return SimpleNamespace(
            scheduler=SchedulerApplicationCapability(
                state_store=execution_state_store,
                plan=plan,
                capability_port=Catalog(),
                invocation_port=invoker,
                artifact_store=artifacts,
            )
        )

    entry = SchedulerProductionEntry(
        session_store=store,
        execution_repository=repository,
        execution_path_router=router,
        scheduler_composer=compose,
        id_generator=lambda: "new-execution",
    )
    return entry, store, repository, bindings, invoker


def test_plan_mapper_is_deterministic_and_contains_full_interview_dag():
    first = build_interview_execution(session_id="session-1", plan=_plan())
    second = build_interview_execution(session_id="session-1", plan=_plan())

    assert first == second
    execution_plan, state = first
    assert [task.skill for task in execution_plan.task_definitions] == [
        "generate-main-question",
        "evaluate-answer",
        None,
        "evaluate-interview",
        "generate-report",
    ]
    assert execution_plan.task_definitions[2].task_kind == (
        "QUESTION_RESOLUTION_GATE"
    )
    assert state.execution_id == "session-1"
    assert state.artifact_refs[0].artifact_type == "interview-plan-artifact"


def test_every_new_start_claims_new_and_bootstraps_scheduler_wait():
    entry, store, repository, bindings, invoker = _runtime()

    turn = entry.start(
        _plan(),
        job_description="backend",
        resume_text="built services",
        job_tags=["backend"],
    )

    assert isinstance(repository, SchedulerExecutionRepository)
    assert turn.session_id == "new-execution"
    assert bindings.get(turn.session_id).path == "NEW"
    assert repository.load(turn.session_id).execution_status == "WAITING"
    assert repository.load(turn.session_id).current_wait_handle.question_id == "q1"
    assert invoker.calls[0][1] == "generate-main-question"
    assert store.get(turn.session_id)["status"] == "active"


def test_scheduler_state_and_plan_survive_entry_recomposition():
    entry, _store, repository, _bindings, invoker = _runtime()
    entry.start(
        _plan(),
        job_description="backend",
        resume_text="built services",
        job_tags=[],
    )
    before = repository.load("new-execution")

    entry.ensure_bootstrapped("new-execution")

    assert repository.load_plan("new-execution").execution_id == "new-execution"
    assert repository.load("new-execution") == before
    assert len(invoker.calls) == 1


def test_new_snapshot_routes_to_scheduler_and_never_old_workflow():
    entry, store, _repository, bindings, _invoker = _runtime()
    turn = entry.start(
        _plan(),
        job_description="backend",
        resume_text="built services",
        job_tags=[],
    )
    application = InterviewApplicationService(
        store=store,
        workflow_service_factory=lambda: WorkflowMustNotRun(),
        publisher=SimpleNamespace(publish=lambda _event: None),
        report_job_store_factory=lambda: SimpleNamespace(),
        execution_path_router=ExecutionPathRouter(bindings),
        scheduler_entry_factory=lambda: entry,
    )

    snapshot = application.snapshot(turn.session_id)

    assert snapshot["orchestration_path"] == "NEW"
    assert snapshot["current_question"]["id"] == "q1"
    assert snapshot["scheduler_revision"] == entry.execution_repository.load(
        turn.session_id
    ).revision


def test_completed_scheduler_finish_replays_projection_without_legacy_transition():
    entry, store, repository, _bindings, _invoker = _runtime()
    turn = entry.start(
        _plan(),
        job_description="backend",
        resume_text="built services",
        job_tags=[],
    )
    command = SessionCommand(
        session_id=turn.session_id,
        command_type="finish",
    )

    first = entry.execute(command)
    completed = repository.load(turn.session_id)
    second = entry.execute(command)

    assert first.status == second.status == "finished"
    assert completed.execution_status == "COMPLETED"
    assert repository.load(turn.session_id) == completed
    assert store.get(turn.session_id)["messages"] == []
