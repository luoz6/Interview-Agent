from __future__ import annotations

from types import SimpleNamespace

from app.a2a.contracts.evaluation import EvaluationArtifactPayload, EvaluationArtifactV2
from app.a2a.contracts.evaluation_set import EvaluationArtifactSetPayload
from app.a2a.contracts.main_question import MainQuestionArtifactPayload
from app.a2a.contracts.report import ReportArtifactPayload
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


class _Catalog:
    def resolve(self, *, agent_id, skill, capability_version=None):
        metadata = {
            "generate-main-question": ("generate-main-question-request", "main-question-artifact", "1.0"),
            "evaluate-answer": ("evaluate-answer-request", "evaluation-artifact", "evaluation-artifact-v2"),
            "evaluate-interview": ("evaluate-interview-request", "evaluation-artifact-set", "1.0"),
            "generate-report": ("generate-report-request", "report-artifact", "1.0"),
        }.get(skill)
        if metadata is None:
            return None
        request, artifact, version = metadata
        return CapabilityDescriptor(
            agent_id=agent_id, skill=skill, description=skill,
            request_contract_id=request, request_contract_version="v1",
            output_artifact_type=artifact, output_artifact_version=version,
            capability_version="v1",
        )

    def validate_compatibility(self, **kwargs):
        return self.resolve(agent_id=kwargs["agent_id"], skill=kwargs["skill"]) is not None


class _Invoker:
    def __init__(self, *, always_degraded: bool = False):
        self.calls: list[str] = []
        self.reviews = 0
        self.always_degraded = always_degraded

    def invoke(self, *, agent_id, skill, request, execution_context=None):
        self.calls.append(skill)
        if skill == "generate-main-question":
            return MainQuestionArtifactPayload(
                question_id=request.intent["question_id"],
                question_text=request.intent["fixed_question_text"],
                render_mode="fixed", reason_code="fixed_plan_question",
            )
        if skill == "evaluate-answer":
            self.reviews += 1
            if self.always_degraded or self.reviews == 1:
                return EvaluationArtifactV2(
                    question_id="q1", answer_artifact_ref=request.answer_artifact_ref,
                    question_artifact_ref=request.question_artifact_ref,
                    evaluation_status="DEGRADED", evidence_status="UNDETERMINED",
                    summary="provider unavailable", evaluation_policy_version="review-policy-v2",
                )
            return EvaluationArtifactV2(
                question_id="q1", answer_artifact_ref=request.answer_artifact_ref,
                question_artifact_ref=request.question_artifact_ref,
                evaluation_status="EVALUATED", evidence_status="SUFFICIENT", score=80,
                summary="sufficient", evaluation_policy_version="review-policy-v2",
            )
        if skill == "evaluate-interview":
            return EvaluationArtifactSetPayload(evaluations=[
                EvaluationArtifactPayload(
                    question_id="q1", score=80,
                    evaluation_policy_version="review-policy-v1",
                )
            ])
        if skill == "generate-report":
            return ReportArtifactPayload(
                session_id=request.session_id, summary="complete",
                dimension_scores={}, report_policy_version="report-policy-v1",
            )
        raise AssertionError(f"unexpected skill: {skill}")


def _runtime(*, always_degraded: bool = False, question_count: int = 1):
    sessions = InterviewSessionStore()
    repository = InMemorySchedulerExecutionRepository()
    artifacts = InMemoryExecutionArtifactStore()
    invoker = _Invoker(always_degraded=always_degraded)

    def compose(*, plan, execution_state_store, **_kwargs):
        return SimpleNamespace(scheduler=SchedulerApplicationCapability(
            state_store=execution_state_store, plan=plan,
            capability_port=_Catalog(), invocation_port=invoker,
            artifact_store=artifacts, evaluation_state_provider=sessions.get,
        ))

    entry = SchedulerProductionEntry(
        session_store=sessions, execution_repository=repository,
        execution_path_router=ExecutionPathRouter(InMemoryExecutionPathBindingStore()),
        scheduler_composer=compose, id_generator=lambda: "cf05",
    )
    plan = InterviewPlan(
        title="CF05",
        questions=[
            InterviewQuestion(
                id=f"q{position}",
                kind="technical",
                prompt=f"Explain backpressure scenario {position}.",
                focus="backpressure",
            )
            for position in range(1, question_count + 1)
        ],
    )
    return entry, repository, artifacts, invoker, plan


def test_degraded_review_retries_without_followup():
    entry, repository, _artifacts, invoker, plan = _runtime()
    turn = entry.start(plan, job_description="backend", resume_text="systems", job_tags=[])
    entry.execute(SessionCommand.answer(turn.session_id, "Bound demand.", command_id="answer-1"))

    assert invoker.calls == [
        "generate-main-question", "evaluate-answer", "evaluate-answer",
        "evaluate-interview", "generate-report",
    ]
    assert repository.load(turn.session_id).retries_used == 1


def test_early_finish_runs_final_pipeline_before_completion():
    entry, repository, artifacts, invoker, plan = _runtime()
    turn = entry.start(plan, job_description="backend", resume_text="systems", job_tags=[])
    entry.execute(SessionCommand(session_id=turn.session_id, command_type="finish"))

    state = repository.load(turn.session_id)
    assert state.execution_status == "COMPLETED"
    assert state.completion_reason == "USER_FINISHED_EARLY"
    report_ref = next(ref for ref in state.artifact_refs if ref.artifact_type == "report-artifact")
    assert artifacts.exists(report_ref.artifact_ref)
    assert invoker.calls[-2:] == ["evaluate-interview", "generate-report"]


def test_degraded_review_exhaustion_resolves_gap_and_continues_without_followup():
    entry, repository, _artifacts, invoker, plan = _runtime(
        always_degraded=True,
        question_count=2,
    )
    turn = entry.start(
        plan,
        job_description="backend",
        resume_text="systems",
        job_tags=[],
    )

    entry.execute(
        SessionCommand.answer(
            turn.session_id,
            "Bound demand.",
            command_id="answer-degraded-exhausted",
        )
    )

    state = repository.load(turn.session_id)
    assert invoker.calls == [
        "generate-main-question",
        "evaluate-answer",
        "evaluate-answer",
        "generate-main-question",
    ]
    assert "generate-followup" not in invoker.calls
    assert state.current_wait_handle is not None
    assert state.current_wait_handle.question_id == "q2"
    assert state.unresolved_gaps[-1]["reason_code"] == "evaluation_degraded"
