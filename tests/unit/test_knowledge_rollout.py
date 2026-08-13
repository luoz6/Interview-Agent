from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.application.knowledge.shadow_service import RetrievalShadowService
from app.domain.knowledge.models import KnowledgeChunk
from app.domain.knowledge.retrieval import (
    RetrievalAvailability,
    RetrievalCandidate,
    RetrievalIntent,
    RetrievalRequest,
    RetrievalResult,
    RetrievalTrace,
)
from app.domain.knowledge.rollout import (
    KnowledgeCanaryObservation,
    KnowledgeCanaryStageEvidence,
    KnowledgeCanaryRecommendation,
    KnowledgeCanaryRunbook,
    KnowledgeEngine,
    KnowledgeRollbackDrillEvidence,
    assign_knowledge_engine,
    evaluate_knowledge_canary,
    evaluate_knowledge_canary_progression,
    evaluate_knowledge_rollback_drill,
    plan_knowledge_rollback,
    resolve_knowledge_engine_assignment,
)


def test_same_session_and_assignment_version_is_stable():
    values = {
        assign_knowledge_engine(
            "session-1", rollout_percent=20, assignment_version="assignment-v1"
        )
        for _ in range(20)
    }
    assert len(values) == 1
    assignment = values.pop()
    assert assignment.session_id_sha256 != "session-1"
    assert assignment.assignment_version == "assignment-v1"


def test_rollback_applies_to_new_assignment_without_rewriting_existing_one():
    existing = assign_knowledge_engine(
        "session-existing", rollout_percent=100, assignment_version="assignment-v1"
    )
    new = assign_knowledge_engine(
        "session-new", rollout_percent=0, assignment_version="assignment-v1"
    )
    persisted = resolve_knowledge_engine_assignment(
        "session-existing",
        rollout_percent=0,
        assignment_version="assignment-v2",
        existing=existing,
    )
    assert existing.engine == KnowledgeEngine.HYBRID_V2
    assert new.engine == KnowledgeEngine.LEGACY
    assert persisted is existing


def test_assignment_validation_fails_closed():
    with pytest.raises(ValueError, match="between 0 and 100"):
        assign_knowledge_engine(
            "session", rollout_percent=101, assignment_version="v1"
        )


def _result(engine: str, ids: tuple[str, ...]) -> RetrievalResult:
    chunks = [
        KnowledgeChunk(
            chunk_id=item,
            title=item,
            content=f"private body {item}",
            source_type="theory",
            domain="redis",
            tags=["redis"],
            metadata={"content_sha256": "a" * 64},
        )
        for item in ids
    ]
    return RetrievalResult(
        request_id="request-1",
        availability=RetrievalAvailability.AVAILABLE,
        candidates=[
            RetrievalCandidate(chunk=chunk, rerank_rank=rank)
            for rank, chunk in enumerate(chunks, 1)
        ],
        selected_evidence=chunks[:1],
        trace=RetrievalTrace(
            request_id="request-1",
            profile_id="shadow",
            profile_version="v1",
            latency_ms=4,
        ),
        retrieval_engine_version=engine,
        profile_version="v1",
        latency_ms=4,
    )


def test_shadow_comparison_is_privacy_safe_and_has_no_binding_writer():
    class Engine:
        def __init__(self, result):
            self.result = result

        def retrieve(self, request, profile):
            return self.result

    service = RetrievalShadowService(
        Engine(_result("compatibility-v1", ("legacy-id",))),
        Engine(_result("hybrid-v2", ("hybrid-id",))),
    )
    request = RetrievalRequest(
        request_id="request-1",
        query_text="PRIVATE ANSWER TEXT",
        intent=RetrievalIntent.SHADOW,
        profile_id="shadow",
    )
    formal_result, comparison = service.compare(
        request,
        legacy_profile=SimpleNamespace(),
        candidate_profile=SimpleNamespace(),
    )
    serialized = comparison.model_dump_json()
    assert formal_result.retrieval_engine_version == "compatibility-v1"
    assert comparison.selected_evidence_changed is True
    assert comparison.legacy.candidate_ids == ("legacy-id",)
    assert comparison.candidate.candidate_ids == ("hybrid-id",)
    assert "PRIVATE ANSWER TEXT" not in serialized
    assert "private body" not in serialized
    assert not hasattr(service, "binding_writer")


def test_shadow_candidate_failure_returns_formal_legacy_result():
    class Legacy:
        def retrieve(self, request, profile):
            return _result("compatibility-v1", ("legacy-id",))

    class Candidate:
        ENGINE_VERSION = "hybrid-v2"

        def retrieve(self, request, profile):
            raise RuntimeError("provider leaked detail")

    request = RetrievalRequest(
        request_id="request-1",
        query_text="PRIVATE ANSWER TEXT",
        intent=RetrievalIntent.SHADOW,
        profile_id="shadow",
    )

    formal_result, failure = RetrievalShadowService(Legacy(), Candidate()).compare(
        request,
        legacy_profile=SimpleNamespace(),
        candidate_profile=SimpleNamespace(),
    )

    assert formal_result.retrieval_engine_version == "compatibility-v1"
    assert failure.reason_code == "shadow_candidate_failed"
    assert "provider leaked detail" not in failure.model_dump_json()


def _runbook() -> KnowledgeCanaryRunbook:
    return KnowledgeCanaryRunbook(
        runbook_version="knowledge-canary-2026-08-12-v1",
        minimum_samples=200,
        minimum_observation_seconds=86_400,
        max_unavailable_rate=0.01,
        profile_p95_budgets_ms={"followup": 800},
    )


def test_canary_requires_registered_step_sample_window_and_latency_budget():
    decision = evaluate_knowledge_canary(
        KnowledgeCanaryObservation(
            rollout_percent=2,
            sample_count=199,
            observation_seconds=100,
            unavailable_count=0,
            profile_id="followup",
            legacy_p95_latency_ms=700,
            candidate_p95_latency_ms=900,
            privacy_audit_passed=True,
            evidence_replay_stable=True,
        ),
        _runbook(),
    )
    assert decision.recommendation == KnowledgeCanaryRecommendation.HOLD
    assert decision.reasons == (
        "insufficient_sample",
        "latency_budget_exceeded",
        "observation_window_incomplete",
        "relative_latency_budget_exceeded",
        "unregistered_rollout_step",
    )


def test_canary_rolls_back_on_replay_privacy_or_error_budget_regression():
    decision = evaluate_knowledge_canary(
        KnowledgeCanaryObservation(
            rollout_percent=5,
            sample_count=200,
            observation_seconds=86_400,
            unavailable_count=3,
            profile_id="followup",
            legacy_p95_latency_ms=700,
            candidate_p95_latency_ms=700,
            critical_regressions=1,
            privacy_audit_passed=False,
            evidence_replay_stable=False,
        ),
        _runbook(),
    )
    assert decision.recommendation == KnowledgeCanaryRecommendation.ROLL_BACK
    assert decision.reasons == (
        "critical_regression",
        "evidence_replay_unstable",
        "privacy_audit_failed",
        "unavailable_error_budget_exceeded",
    )


def test_healthy_canary_and_rollback_replay_contract():
    decision = evaluate_knowledge_canary(
        KnowledgeCanaryObservation(
            rollout_percent=5,
            sample_count=200,
            observation_seconds=86_400,
            unavailable_count=2,
            profile_id="followup",
            legacy_p95_latency_ms=700,
            candidate_p95_latency_ms=800,
            privacy_audit_passed=True,
            evidence_replay_stable=True,
        ),
        _runbook(),
    )
    rollback = plan_knowledge_rollback(
        reason_codes=("critical_regression",),
        evidence_bindings_replayable=True,
        reports_recoverable=True,
    )
    assert decision.recommendation == KnowledgeCanaryRecommendation.ELIGIBLE_TO_CONTINUE
    assert rollback.new_session_rollout_percent == 0
    assert rollback.existing_assignment_preserved is True
    assert rollback.evidence_bindings_replayable is True
    assert rollback.reports_recoverable is True


def test_runbook_rejects_non_monotonic_steps():
    with pytest.raises(ValidationError, match="unique and increasing"):
        KnowledgeCanaryRunbook(
            runbook_version="v1",
            rollout_steps=(0, 5, 1),
            minimum_samples=1,
            minimum_observation_seconds=1,
            max_unavailable_rate=0,
            profile_p95_budgets_ms={"followup": 1},
        )


def _healthy_observation(step: int) -> KnowledgeCanaryObservation:
    return KnowledgeCanaryObservation(
        rollout_percent=step,
        sample_count=200,
        observation_seconds=86_400,
        unavailable_count=0,
        profile_id="followup",
        legacy_p95_latency_ms=700,
        candidate_p95_latency_ms=700,
        privacy_audit_passed=True,
        evidence_replay_stable=True,
    )


def _stage(step: int) -> KnowledgeCanaryStageEvidence:
    observation = _healthy_observation(step)
    return KnowledgeCanaryStageEvidence(
        observation=observation,
        decision=evaluate_knowledge_canary(observation, _runbook()),
        observation_artifact_sha256=f"{step + 1:064x}"[-64:],
    )


def test_canary_progression_cannot_skip_directly_to_higher_stage():
    progression = evaluate_knowledge_canary_progression((_stage(100),), _runbook())

    assert progression.recommendation == KnowledgeCanaryRecommendation.HOLD
    assert progression.reasons == ("canary_stage_sequence_gap",)
    assert progression.next_step == 1


def test_canary_progression_requires_every_stage_before_full_eligibility():
    partial = evaluate_knowledge_canary_progression(
        tuple(_stage(step) for step in (1, 5, 20)),
        _runbook(),
    )
    complete = evaluate_knowledge_canary_progression(
        tuple(_stage(step) for step in (1, 5, 20, 50, 100)),
        _runbook(),
    )

    assert partial.recommendation == KnowledgeCanaryRecommendation.HOLD
    assert partial.completed_steps == (0, 1, 5, 20)
    assert partial.next_step == 50
    assert complete.recommendation == KnowledgeCanaryRecommendation.ELIGIBLE_TO_CONTINUE
    assert complete.completed_steps == (0, 1, 5, 20, 50, 100)
    assert complete.next_step is None


def test_rollback_drill_requires_every_recovery_step_and_reason():
    incomplete = evaluate_knowledge_rollback_drill(
        KnowledgeRollbackDrillEvidence(
            hybrid_was_enabled=True,
            regression_detected=True,
            new_sessions_switched_to_legacy=True,
            existing_assignment_interpretable=False,
            evidence_bindings_replayable=True,
            reports_recoverable=True,
            reason_codes=(),
            drill_artifact_sha256="a" * 64,
        )
    )
    complete = evaluate_knowledge_rollback_drill(
        KnowledgeRollbackDrillEvidence(
            hybrid_was_enabled=True,
            regression_detected=True,
            new_sessions_switched_to_legacy=True,
            existing_assignment_interpretable=True,
            evidence_bindings_replayable=True,
            reports_recoverable=True,
            reason_codes=("critical_regression",),
            drill_artifact_sha256="b" * 64,
        )
    )

    assert incomplete.passed is False
    assert incomplete.missing_steps == (
        "existing_assignment_interpretable",
        "reason_codes",
    )
    assert complete.passed is True
