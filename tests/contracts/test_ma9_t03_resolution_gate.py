from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.a2a.contracts.evaluation import (
    EvaluationArtifactV2,
    EvaluationGapPayload,
)
from app.a2a.contracts.main_question import MainQuestionArtifactPayload
from app.a2a.contracts.followup import FollowupArtifactPayload
from app.adapters.memory.execution_artifacts import InMemoryExecutionArtifactStore
from app.adapters.memory.execution_path_binding import InMemoryExecutionPathBindingStore
from app.adapters.memory.scheduler_execution import InMemorySchedulerExecutionRepository
from app.adapters.memory.session_store import InterviewSessionStore
from app.application.interview.orchestration_cutover import ExecutionPathRouter
from app.application.interview.scheduler_production_entry import SchedulerProductionEntry
from app.application.scheduling import (
    DeterministicSchedulerPolicy,
    SchedulerApplicationCapability,
)
from app.domain.interview.commands import SessionCommand
from app.domain.interview.prep import InterviewPlan, InterviewQuestion
from app.domain.interview.scheduling import CapabilityDescriptor


class _Catalog:
    def __init__(self) -> None:
        self.capabilities = {
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
            ("interview-examiner", "generate-followup"): CapabilityDescriptor(
                agent_id="interview-examiner",
                skill="generate-followup",
                description="follow-up",
                request_contract_id="generate-followup-request",
                request_contract_version="v1",
                output_artifact_type="followup-artifact",
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
    def __init__(self, evidence_status: str | list[str]) -> None:
        self.evidence_statuses = (
            list(evidence_status)
            if isinstance(evidence_status, list)
            else [evidence_status]
        )
        self.calls: list[str] = []

    def invoke(self, *, agent_id, skill, request, execution_context=None):
        self.calls.append(skill)
        if skill == "generate-main-question":
            return MainQuestionArtifactPayload(
                question_id=request.intent["question_id"],
                question_text=request.intent["fixed_question_text"],
                render_mode="fixed",
                reason_code="fixed_plan_question",
            )
        if skill == "generate-followup":
            return FollowupArtifactPayload(
                question_id=request.question_id,
                gap_id=request.gap_id,
                followup_text="How do you handle failure?",
                reason_code=request.reason_code,
                focus=request.focus,
                policy_version=request.policy_version,
                evidence_ids=list(request.evidence_ids),
            )
        assert request.answer_artifact_ref.startswith("answer/sha256:")
        assert request.question_artifact_ref
        evidence_status = self.evidence_statuses.pop(0)
        if evidence_status == "SUFFICIENT":
            return EvaluationArtifactV2(
                question_id=request.question_id,
                answer_artifact_ref=request.answer_artifact_ref,
                question_artifact_ref=request.question_artifact_ref,
                evaluation_status="EVALUATED",
                evidence_status="SUFFICIENT",
                score=85,
                summary="Enough evidence.",
                evaluation_policy_version="review-policy-v2",
            )
        return EvaluationArtifactV2(
            question_id=request.question_id,
            answer_artifact_ref=request.answer_artifact_ref,
            question_artifact_ref=request.question_artifact_ref,
            evaluation_status="EVALUATED",
            evidence_status="INSUFFICIENT",
            gap=EvaluationGapPayload(
                gap_id="gap:q1:depth",
                type="depth",
                focus="failure handling",
                reason="No failure-mode evidence.",
            ),
            summary="More evidence required.",
            evaluation_policy_version="review-policy-v2",
        )


def _entry(evidence_status: str | list[str]):
    session_store = InterviewSessionStore()
    repository = InMemorySchedulerExecutionRepository()
    router = ExecutionPathRouter(InMemoryExecutionPathBindingStore())
    invoker = _Invoker(evidence_status)
    artifacts = InMemoryExecutionArtifactStore()

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

    return (
        SchedulerProductionEntry(
            session_store=session_store,
            execution_repository=repository,
            execution_path_router=router,
            scheduler_composer=compose,
            id_generator=lambda: "t03-resolution",
        ),
        repository,
        invoker,
    )


def _plan():
    return InterviewPlan(
        title="T03",
        questions=[
            InterviewQuestion(
                id=f"q{position}",
                kind="technical",
                prompt=f"Question {position}?",
                focus="systems",
            )
            for position in (1, 2)
        ],
    )


def _answer(entry):
    turn = entry.start(
        _plan(),
        job_description="backend",
        resume_text="systems",
        job_tags=[],
    )
    entry.execute(
        SessionCommand.answer(
            turn.session_id,
            "I use explicit bounds.",
            command_id="answer-q1",
        )
    )
    return turn.session_id


def test_insufficient_evaluation_keeps_gate_unresolved_and_blocks_main_q2():
    entry, repository, invoker = _entry("INSUFFICIENT")
    execution_id = _answer(entry)
    state = repository.load(execution_id)
    plan = repository.load_plan(execution_id)

    assert state.task_state("evaluate:1:q1").status == "COMPLETED"
    assert state.task_state("resolve:1:q1").status == "PENDING"
    assert state.task_state("main:2:q2").status == "PENDING"
    assert state.current_wait_handle.task_id == "followup:q1:1"
    decision = DeterministicSchedulerPolicy().decide(plan=plan, state=state)
    assert decision.action == "WAIT_USER"
    assert invoker.calls.count("evaluate-answer") == 1


def test_sufficient_evaluation_completes_gate_without_agent_invocation():
    entry, repository, invoker = _entry("SUFFICIENT")
    execution_id = _answer(entry)
    state = repository.load(execution_id)

    assert state.task_state("evaluate:1:q1").status == "COMPLETED"
    assert state.task_state("resolve:1:q1").status == "COMPLETED"
    assert all(call != "question-resolution" for call in invoker.calls)


def test_evaluation_v2_rejects_illegal_status_matrix():
    with pytest.raises(ValueError, match="requires score"):
        EvaluationArtifactV2(
            question_id="q1",
            answer_artifact_ref="answer-1",
            question_artifact_ref="question-1",
            evaluation_status="EVALUATED",
            evidence_status="SUFFICIENT",
            summary="invalid",
            evaluation_policy_version="review-policy-v2",
        )


def test_followup_pair_is_atomic_idempotent_and_preserves_invocation_order():
    entry, repository, invoker = _entry(["INSUFFICIENT", "SUFFICIENT"])
    execution_id = _answer(entry)
    after_pair = repository.load(execution_id)
    dynamic = {task.task_id: task for task in after_pair.dynamic_task_definitions}

    assert set(dynamic) == {"followup:q1:1", "evaluate-followup:q1:1"}
    assert after_pair.followups_total_used == 1
    assert after_pair.followups_by_question == {"q1": 1}
    assert after_pair.replans_used == 1
    assert after_pair.current_wait_handle.task_id == "followup:q1:1"
    assert (
        after_pair.register_followup_pair(
            expected_revision=after_pair.revision,
            question_id="q1",
            followup=dynamic["followup:q1:1"],
            evaluation=dynamic["evaluate-followup:q1:1"],
        )
        is after_pair
    )

    entry.execute(
        SessionCommand.answer(
            execution_id,
            "I shed load and retry with jitter.",
            command_id="answer-q1-followup-1",
        )
    )

    final = repository.load(execution_id)
    assert final.task_state("evaluate-followup:q1:1").status == "COMPLETED"
    assert final.task_state("resolve:1:q1").status == "COMPLETED"
    assert final.current_wait_handle.question_id == "q2"
    assert invoker.calls == [
        "generate-main-question",
        "evaluate-answer",
        "generate-followup",
        "evaluate-answer",
        "generate-main-question",
    ]


def test_followup_budget_exhaustion_records_gap_resolves_and_releases_next_main():
    entry, repository, invoker = _entry(["INSUFFICIENT", "INSUFFICIENT"])
    execution_id = _answer(entry)

    entry.execute(
        SessionCommand.answer(
            execution_id,
            "I still do not provide enough evidence.",
            command_id="answer-q1-followup-1",
        )
    )

    state = repository.load(execution_id)
    assert state.followups_total_used == state.replans_used == 1
    assert state.followups_by_question == {"q1": 1}
    assert all(task.task_id != "followup:q1:2" for task in state.dynamic_task_definitions)
    assert state.task_state("resolve:1:q1").status == "COMPLETED"
    assert state.current_wait_handle.question_id == "q2"
    assert state.unresolved_gaps == (
        {
            "question_id": "q1",
            "evaluation_task_id": "evaluate-followup:q1:1",
            "reason_code": "followup_budget_exhausted",
            "gap_id": "gap:q1:depth",
            "type": "depth",
            "focus": "failure handling",
            "reason": "No failure-mode evidence.",
        },
    )
    assert invoker.calls[-1] == "generate-main-question"
