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
    def __init__(self) -> None:
        self.capabilities = {
            ("interview-examiner", "generate-main-question"): self._capability(
                "interview-examiner",
                "generate-main-question",
                "generate-main-question-request",
                "main-question-artifact",
                "1.0",
            ),
            ("interview-reviewer", "evaluate-answer"): self._capability(
                "interview-reviewer",
                "evaluate-answer",
                "evaluate-answer-request",
                "evaluation-artifact",
                "evaluation-artifact-v2",
            ),
            ("interview-reviewer", "evaluate-interview"): self._capability(
                "interview-reviewer",
                "evaluate-interview",
                "evaluate-interview-request",
                "evaluation-artifact-set",
                "1.0",
            ),
            ("report-coach", "generate-report"): self._capability(
                "report-coach",
                "generate-report",
                "generate-report-request",
                "report-artifact",
                "1.0",
            ),
        }

    @staticmethod
    def _capability(agent_id, skill, request, output, version):
        return CapabilityDescriptor(
            agent_id=agent_id,
            skill=skill,
            description=skill,
            request_contract_id=request,
            request_contract_version="v1",
            output_artifact_type=output,
            output_artifact_version=version,
            capability_version="v1",
        )

    def resolve(self, *, agent_id, skill, capability_version=None):
        return self.capabilities.get((agent_id, skill))

    def validate_compatibility(self, **kwargs):
        return self.resolve(agent_id=kwargs["agent_id"], skill=kwargs["skill"]) is not None


class _Invoker:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def invoke(self, *, agent_id, skill, request, execution_context=None):
        self.calls.append(skill)
        if skill == "generate-main-question":
            return MainQuestionArtifactPayload(
                question_id="q1",
                question_text="Explain backpressure.",
                render_mode="fixed",
                reason_code="fixed_plan_question",
            )
        if skill == "evaluate-answer":
            return EvaluationArtifactV2(
                question_id="q1",
                answer_artifact_ref=request.answer_artifact_ref,
                question_artifact_ref=request.question_artifact_ref,
                evaluation_status="EVALUATED",
                evidence_status="SUFFICIENT",
                score=88,
                summary="Sufficient.",
                evaluation_policy_version="review-policy-v2",
            )
        if skill == "evaluate-interview":
            assert [item["content"] for item in request.state["messages"]] == [
                "Explain backpressure.",
                "Bound producers and propagate demand.",
            ]
            return EvaluationArtifactSetPayload(
                evaluations=[
                    EvaluationArtifactPayload(
                        question_id="q1",
                        score=88,
                        evaluation_policy_version="review-policy-v1",
                    )
                ]
            )
        assert skill == "generate-report"
        assert request.evaluation_artifacts[0]["question_id"] == "q1"
        assert request.evaluation_artifacts[0]["score"] == 88
        return ReportArtifactPayload(
            session_id=request.session_id,
            summary="Strong backpressure fundamentals.",
            dimension_scores={"technical": 88},
            strengths=["Explains bounded demand."],
            report_policy_version="report-policy-v1",
        )


def test_all_resolutions_dispatch_final_reviewer_then_report_coach_and_complete():
    session_store = InterviewSessionStore()
    repository = InMemorySchedulerExecutionRepository()
    artifacts = InMemoryExecutionArtifactStore()
    invoker = _Invoker()

    def compose(*, plan, initial_state, execution_state_store, **_kwargs):
        return SimpleNamespace(
            scheduler=SchedulerApplicationCapability(
                state_store=execution_state_store,
                plan=plan,
                capability_port=_Catalog(),
                invocation_port=invoker,
                artifact_store=artifacts,
                evaluation_state_provider=session_store.get,
            )
        )

    entry = SchedulerProductionEntry(
        session_store=session_store,
        execution_repository=repository,
        execution_path_router=ExecutionPathRouter(InMemoryExecutionPathBindingStore()),
        scheduler_composer=compose,
        id_generator=lambda: "t06-final",
    )
    started = entry.start(
        InterviewPlan(
            title="T06",
            questions=[
                InterviewQuestion(
                    id="q1",
                    kind="technical",
                    prompt="Explain backpressure.",
                    focus="backpressure",
                )
            ],
        ),
        job_description="backend",
        resume_text="distributed systems",
        job_tags=[],
    )

    completed = entry.execute(
        SessionCommand.answer(
            started.session_id,
            "Bound producers and propagate demand.",
            command_id="answer-q1",
        )
    )
    state = repository.load(started.session_id)

    assert completed.status == "finished"
    assert state.execution_status == "COMPLETED"
    assert state.task_state("evaluate:interview").status == "COMPLETED"
    assert state.task_state("report:interview").status == "COMPLETED"
    assert invoker.calls == [
        "generate-main-question",
        "evaluate-answer",
        "evaluate-interview",
        "generate-report",
    ]
    assert [ref.artifact_type for ref in state.artifact_refs][-2:] == [
        "evaluation-artifact-set",
        "report-artifact",
    ]
    report_ref = state.artifact_refs[-1].artifact_ref
    report = artifacts.get_required(report_ref)
    assert report.summary == "Strong backpressure fundamentals."
    assert entry.snapshot(started.session_id)["status"] == "finished"
