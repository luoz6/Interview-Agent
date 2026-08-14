from __future__ import annotations

import hashlib
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.knowledge.engine import (
    KnowledgeEngine,
    LegacyKnowledgeEngineAssignment,
)


KnowledgeEngineAssignment = LegacyKnowledgeEngineAssignment


def assign_knowledge_engine(
    session_id: str,
    *,
    rollout_percent: int,
    assignment_version: str,
    candidate_engine: KnowledgeEngine = KnowledgeEngine.HYBRID_V2,
) -> KnowledgeEngineAssignment:
    if not session_id.strip():
        raise ValueError("session_id must not be blank")
    if not 0 <= rollout_percent <= 100:
        raise ValueError("rollout_percent must be between 0 and 100")
    if not assignment_version.strip():
        raise ValueError("assignment_version must not be blank")
    session_hash = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
    bucket_hash = hashlib.sha256(
        f"{assignment_version}:{session_id}".encode("utf-8")
    ).hexdigest()
    bucket = int(bucket_hash[:8], 16) % 100
    return KnowledgeEngineAssignment(
        session_id_sha256=session_hash,
        engine=(candidate_engine if bucket < rollout_percent else KnowledgeEngine.LEGACY),
        assignment_version=assignment_version,
        bucket=bucket,
        rollout_percent=rollout_percent,
    )


def resolve_knowledge_engine_assignment(
    session_id: str,
    *,
    rollout_percent: int,
    assignment_version: str,
    existing: KnowledgeEngineAssignment | None = None,
) -> KnowledgeEngineAssignment:
    if existing is not None:
        expected_hash = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
        if existing.session_id_sha256 != expected_hash:
            raise ValueError("existing assignment does not belong to session")
        return existing
    return assign_knowledge_engine(
        session_id,
        rollout_percent=rollout_percent,
        assignment_version=assignment_version,
    )


class KnowledgeCanaryRunbook(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    runbook_version: str
    rollout_steps: tuple[int, ...] = (0, 1, 5, 20, 50, 100)
    minimum_samples: int = Field(ge=1)
    minimum_observation_seconds: int = Field(ge=1)
    max_unavailable_rate: float = Field(ge=0, le=1)
    profile_p95_budgets_ms: dict[str, float]
    max_relative_p95_multiplier: float = Field(default=1.25, ge=1)
    max_critical_regressions: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_steps(self):
        if not self.runbook_version.strip():
            raise ValueError("runbook_version must not be blank")
        if not self.rollout_steps or self.rollout_steps[0] != 0:
            raise ValueError("rollout steps must begin at zero")
        if any(step < 0 or step > 100 for step in self.rollout_steps):
            raise ValueError("rollout steps must be between 0 and 100")
        if tuple(sorted(set(self.rollout_steps))) != self.rollout_steps:
            raise ValueError("rollout steps must be unique and increasing")
        if not self.profile_p95_budgets_ms or any(
            not profile.strip() or budget <= 0
            for profile, budget in self.profile_p95_budgets_ms.items()
        ):
            raise ValueError("profile P95 budgets must be positive")
        return self


class KnowledgeCanaryObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    rollout_percent: int = Field(ge=0, le=100)
    sample_count: int = Field(ge=0)
    observation_seconds: int = Field(ge=0)
    unavailable_count: int = Field(ge=0)
    profile_id: str
    legacy_p95_latency_ms: float = Field(gt=0)
    candidate_p95_latency_ms: float = Field(ge=0)
    critical_regressions: int = Field(default=0, ge=0)
    privacy_audit_passed: bool = False
    evidence_replay_stable: bool = False


class KnowledgeCanaryRecommendation(StrEnum):
    HOLD = "hold"
    ROLL_BACK = "roll_back"
    ELIGIBLE_TO_CONTINUE = "eligible_to_continue"


class KnowledgeCanaryDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    recommendation: KnowledgeCanaryRecommendation
    reasons: tuple[str, ...] = ()
    runbook_version: str


class KnowledgeCanaryStageEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    observation: KnowledgeCanaryObservation
    decision: KnowledgeCanaryDecision
    observation_artifact_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class KnowledgeCanaryProgressDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    recommendation: KnowledgeCanaryRecommendation
    completed_steps: tuple[int, ...]
    next_step: int | None
    reasons: tuple[str, ...] = ()
    runbook_version: str


def evaluate_knowledge_canary(
    observation: KnowledgeCanaryObservation,
    runbook: KnowledgeCanaryRunbook,
) -> KnowledgeCanaryDecision:
    rollback = []
    if not observation.privacy_audit_passed:
        rollback.append("privacy_audit_failed")
    if observation.critical_regressions > runbook.max_critical_regressions:
        rollback.append("critical_regression")
    if not observation.evidence_replay_stable:
        rollback.append("evidence_replay_unstable")
    unavailable_rate = observation.unavailable_count / max(1, observation.sample_count)
    if unavailable_rate > runbook.max_unavailable_rate:
        rollback.append("unavailable_error_budget_exceeded")
    if rollback:
        return KnowledgeCanaryDecision(
            recommendation=KnowledgeCanaryRecommendation.ROLL_BACK,
            reasons=tuple(sorted(set(rollback))),
            runbook_version=runbook.runbook_version,
        )
    hold = []
    if observation.rollout_percent not in runbook.rollout_steps:
        hold.append("unregistered_rollout_step")
    if observation.sample_count < runbook.minimum_samples:
        hold.append("insufficient_sample")
    if observation.observation_seconds < runbook.minimum_observation_seconds:
        hold.append("observation_window_incomplete")
    absolute_budget = runbook.profile_p95_budgets_ms.get(observation.profile_id)
    if absolute_budget is None:
        hold.append("unregistered_profile")
    elif observation.candidate_p95_latency_ms > absolute_budget:
        hold.append("latency_budget_exceeded")
    if (
        observation.candidate_p95_latency_ms
        > observation.legacy_p95_latency_ms * runbook.max_relative_p95_multiplier
    ):
        hold.append("relative_latency_budget_exceeded")
    return KnowledgeCanaryDecision(
        recommendation=(
            KnowledgeCanaryRecommendation.HOLD
            if hold
            else KnowledgeCanaryRecommendation.ELIGIBLE_TO_CONTINUE
        ),
        reasons=tuple(sorted(set(hold))),
        runbook_version=runbook.runbook_version,
    )


def evaluate_knowledge_canary_progression(
    stage_evidence: tuple[KnowledgeCanaryStageEvidence, ...],
    runbook: KnowledgeCanaryRunbook,
) -> KnowledgeCanaryProgressDecision:
    if not stage_evidence:
        return KnowledgeCanaryProgressDecision(
            recommendation=KnowledgeCanaryRecommendation.HOLD,
            completed_steps=(0,),
            next_step=runbook.rollout_steps[1] if len(runbook.rollout_steps) > 1 else None,
            reasons=("no_canary_stage_evidence",),
            runbook_version=runbook.runbook_version,
        )
    observed_steps = tuple(item.observation.rollout_percent for item in stage_evidence)
    if len(observed_steps) != len(set(observed_steps)):
        raise ValueError("canary progression cannot contain duplicate stages")
    expected_nonzero = runbook.rollout_steps[1 : len(observed_steps) + 1]
    if observed_steps != expected_nonzero:
        return KnowledgeCanaryProgressDecision(
            recommendation=KnowledgeCanaryRecommendation.HOLD,
            completed_steps=(0,),
            next_step=runbook.rollout_steps[1] if len(runbook.rollout_steps) > 1 else None,
            reasons=("canary_stage_sequence_gap",),
            runbook_version=runbook.runbook_version,
        )
    completed = [0]
    for stage in stage_evidence:
        if stage.decision.runbook_version != runbook.runbook_version:
            return KnowledgeCanaryProgressDecision(
                recommendation=KnowledgeCanaryRecommendation.HOLD,
                completed_steps=tuple(completed),
                next_step=stage.observation.rollout_percent,
                reasons=("canary_runbook_version_mismatch",),
                runbook_version=runbook.runbook_version,
            )
        recomputed = evaluate_knowledge_canary(stage.observation, runbook)
        if stage.decision != recomputed:
            return KnowledgeCanaryProgressDecision(
                recommendation=KnowledgeCanaryRecommendation.HOLD,
                completed_steps=tuple(completed),
                next_step=stage.observation.rollout_percent,
                reasons=("canary_decision_evidence_mismatch",),
                runbook_version=runbook.runbook_version,
            )
        if recomputed.recommendation == KnowledgeCanaryRecommendation.ROLL_BACK:
            return KnowledgeCanaryProgressDecision(
                recommendation=KnowledgeCanaryRecommendation.ROLL_BACK,
                completed_steps=tuple(completed),
                next_step=stage.observation.rollout_percent,
                reasons=recomputed.reasons,
                runbook_version=runbook.runbook_version,
            )
        if recomputed.recommendation != KnowledgeCanaryRecommendation.ELIGIBLE_TO_CONTINUE:
            return KnowledgeCanaryProgressDecision(
                recommendation=KnowledgeCanaryRecommendation.HOLD,
                completed_steps=tuple(completed),
                next_step=stage.observation.rollout_percent,
                reasons=recomputed.reasons,
                runbook_version=runbook.runbook_version,
            )
        completed.append(stage.observation.rollout_percent)
    next_step = (
        runbook.rollout_steps[len(completed)]
        if len(completed) < len(runbook.rollout_steps)
        else None
    )
    return KnowledgeCanaryProgressDecision(
        recommendation=(
            KnowledgeCanaryRecommendation.ELIGIBLE_TO_CONTINUE
            if next_step is None
            else KnowledgeCanaryRecommendation.HOLD
        ),
        completed_steps=tuple(completed),
        next_step=next_step,
        reasons=(() if next_step is None else ("next_stage_not_observed",)),
        runbook_version=runbook.runbook_version,
    )


class KnowledgeRollbackDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    new_session_rollout_percent: int = Field(default=0, frozen=True)
    existing_assignment_preserved: bool = True
    evidence_bindings_replayable: bool
    reports_recoverable: bool
    reason_codes: tuple[str, ...]


class KnowledgeRollbackDrillEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    hybrid_was_enabled: bool
    regression_detected: bool
    new_sessions_switched_to_legacy: bool
    existing_assignment_interpretable: bool
    evidence_bindings_replayable: bool
    reports_recoverable: bool
    reason_codes: tuple[str, ...]
    drill_artifact_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class KnowledgeRollbackDrillDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    passed: bool
    missing_steps: tuple[str, ...]
    decision_version: str = "knowledge-rollback-drill-gate-v1"


def plan_knowledge_rollback(
    *,
    reason_codes: tuple[str, ...],
    evidence_bindings_replayable: bool,
    reports_recoverable: bool,
) -> KnowledgeRollbackDecision:
    if not reason_codes:
        raise ValueError("rollback requires at least one reason code")
    return KnowledgeRollbackDecision(
        evidence_bindings_replayable=evidence_bindings_replayable,
        reports_recoverable=reports_recoverable,
        reason_codes=tuple(sorted(set(reason_codes))),
    )


def evaluate_knowledge_rollback_drill(
    evidence: KnowledgeRollbackDrillEvidence,
) -> KnowledgeRollbackDrillDecision:
    missing = []
    for field_name in (
        "hybrid_was_enabled",
        "regression_detected",
        "new_sessions_switched_to_legacy",
        "existing_assignment_interpretable",
        "evidence_bindings_replayable",
        "reports_recoverable",
    ):
        if not getattr(evidence, field_name):
            missing.append(field_name)
    if not evidence.reason_codes:
        missing.append("reason_codes")
    return KnowledgeRollbackDrillDecision(
        passed=not missing,
        missing_steps=tuple(missing),
    )
