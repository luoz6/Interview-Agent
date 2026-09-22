from __future__ import annotations

from types import SimpleNamespace

from app.a2a.contracts.evaluation import EvaluationArtifactV2
from app.a2a.contracts.main_question import MainQuestionArtifactPayload
from app.adapters.memory.execution_artifacts import InMemoryExecutionArtifactStore
from app.adapters.memory.execution_path_binding import InMemoryExecutionPathBindingStore
from app.adapters.memory.scheduler_execution import InMemorySchedulerExecutionRepository
from app.adapters.memory.session_store import InterviewSessionStore
from app.application.interview.orchestration_cutover import ExecutionPathRouter
from app.application.interview.scheduler_production_entry import SchedulerProductionEntry
from app.application.scheduling import SchedulerApplicationCapability
from app.domain.interview.commands import SessionCommand
from app.domain.interview.prep import InterviewPlan, InterviewQuestion
from app.domain.interview.scheduling import CapabilityDescriptor


class _ProjectionOnlySessionStore(InterviewSessionStore):
    def start(self, *args, **kwargs):
        raise AssertionError("legacy start must not run for a NEW execution")

    def submit_answer(self, *args, **kwargs):
        raise AssertionError("legacy submit_answer must not run for a NEW execution")

    def skip(self, *args, **kwargs):
        raise AssertionError("legacy skip must not run for a NEW execution")

    def finish(self, *args, **kwargs):
        raise AssertionError("legacy finish must not run for a NEW execution")


class _Catalog:
    capabilities = {
        ("interview-examiner", "generate-main-question"): CapabilityDescriptor(
            agent_id="interview-examiner",
            skill="generate-main-question",
            description="main",
            request_contract_id="generate-main-question-request",
            request_contract_version="v1",
            output_artifact_type="main-question-artifact",
            output_artifact_version="1.0",
            capability_version="v1",
        ),
        ("interview-reviewer", "evaluate-answer"): CapabilityDescriptor(
            agent_id="interview-reviewer",
            skill="evaluate-answer",
            description="review",
            request_contract_id="evaluate-answer-request",
            request_contract_version="v1",
            output_artifact_type="evaluation-artifact",
            output_artifact_version="evaluation-artifact-v2",
            capability_version="v1",
        ),
    }

    def resolve(self, *, agent_id, skill, capability_version=None):
        return self.capabilities.get((agent_id, skill))

    def validate_compatibility(self, **kwargs):
        return self.resolve(agent_id=kwargs["agent_id"], skill=kwargs["skill"]) is not None


class _Invoker:
    def invoke(self, *, agent_id, skill, request, execution_context=None):
        if skill == "generate-main-question":
            return MainQuestionArtifactPayload(
                question_id=request.intent["question_id"],
                question_text=request.intent["fixed_question_text"],
                render_mode="fixed",
                reason_code="fixed_plan_question",
            )
        return EvaluationArtifactV2(
            question_id=request.question_id,
            answer_artifact_ref=request.answer_artifact_ref,
            question_artifact_ref=request.question_artifact_ref,
            evaluation_status="EVALUATED",
            evidence_status="SUFFICIENT",
            score=90,
            summary="Enough evidence.",
            evaluation_policy_version="review-policy-v2",
        )


def _plan() -> InterviewPlan:
    return InterviewPlan(
        title="Projection",
        questions=[
            InterviewQuestion(
                id="q1",
                kind="technical",
                prompt="Question one?",
                focus="one",
            ),
            InterviewQuestion(
                id="q2",
                kind="system-design",
                prompt="Question two?",
                focus="two",
            ),
        ],
    )


def _entry():
    session_store = _ProjectionOnlySessionStore()
    repository = InMemorySchedulerExecutionRepository()
    artifacts = InMemoryExecutionArtifactStore()
    router = ExecutionPathRouter(InMemoryExecutionPathBindingStore())

    def compose(*, plan, initial_state, execution_state_store, **_kwargs):
        return SimpleNamespace(
            scheduler=SchedulerApplicationCapability(
                state_store=execution_state_store,
                plan=plan,
                capability_port=_Catalog(),
                invocation_port=_Invoker(),
                artifact_store=artifacts,
                evaluation_state_provider=session_store.get,
            )
        )

    return (
        SchedulerProductionEntry(
            session_store=session_store,
            execution_repository=repository,
            execution_path_router=router,
            scheduler_composer=compose,
            id_generator=lambda: "t05-projection",
        ),
        repository,
    )


def test_new_execution_uses_session_store_as_projection_only():
    entry, repository = _entry()

    started = entry.start(
        _plan(),
        job_description="backend",
        resume_text="systems",
        job_tags=[],
    )
    assert started.current_question.id == "q1"

    turn = entry.execute(
        SessionCommand.answer(
            started.session_id,
            "Answer one.",
            command_id="answer-q1",
        )
    )

    assert turn.current_question.id == "q2"
    assert repository.load(started.session_id).current_wait_handle.question_id == "q2"


def test_snapshot_is_read_only_and_rebuilt_from_execution_artifacts():
    entry, repository = _entry()
    started = entry.start(
        _plan(),
        job_description="backend",
        resume_text="systems",
        job_tags=[],
    )
    entry.execute(
        SessionCommand.answer(
            started.session_id,
            "Answer one.",
            command_id="answer-q1",
        )
    )
    before = repository.load(started.session_id)

    snapshot = entry.snapshot(started.session_id)

    assert repository.load(started.session_id) == before
    assert snapshot["state_version"] == before.revision
    assert snapshot["current_question"]["id"] == "q2"
    assert [message["content"] for message in snapshot["messages"]] == [
        "Question one?",
        "Answer one.",
        "Question two?",
    ]
    assert all(task.reason_code != "compatibility_projection" for task in before.task_states)
