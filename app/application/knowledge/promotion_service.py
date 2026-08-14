from __future__ import annotations

from datetime import datetime, timezone

from app.application.knowledge.diagnostic_models import (
    PromotionBlocker,
    PromotionDecision,
)


def current_knowledge_promotion_decision(
    *,
    evaluated_at: datetime | None = None,
) -> PromotionDecision:
    """Return the current versioned release-gate truth for the RAG candidate."""

    timestamp = evaluated_at or datetime.now(timezone.utc)
    blockers = (
        PromotionBlocker(
            code="HUMAN_TUNING_GT_MISSING",
            severity="hard_stop",
            blocks=("candidate_activation", "shadow", "canary", "production"),
            observed_evidence="Human annotators: 0; adjudication is incomplete.",
            required_action="Complete independent tuning annotation and adjudication.",
            last_evaluated_at=timestamp,
        ),
        PromotionBlocker(
            code="NO_EVIDENCE_GATE_FAILED",
            severity="hard_stop",
            blocks=("shadow", "canary", "production"),
            observed_evidence="Machine tuning no-evidence F1 remains below the release gate.",
            required_action="Calibrate and validate evidence sufficiency policy.",
            last_evaluated_at=timestamp,
        ),
        PromotionBlocker(
            code="HYBRID_NOT_BETTER_THAN_LEGACY",
            severity="hard_stop",
            blocks=("candidate_activation", "shadow", "canary", "production"),
            observed_evidence="The frozen weighted-RRF tuning Artifact does not beat Legacy overall.",
            required_action="Re-evaluate on adjudicated tuning Ground Truth before promotion.",
            last_evaluated_at=timestamp,
        ),
        PromotionBlocker(
            code="SEALED_HOLDOUT_MISSING",
            severity="hard_stop",
            blocks=("canary", "production"),
            observed_evidence="Only a previously viewed historical machine holdout is present.",
            required_action="Run a governed sealed holdout after tuning freeze.",
            last_evaluated_at=timestamp,
        ),
        PromotionBlocker(
            code="BUSINESS_BLIND_AB_PENDING",
            severity="hard_stop",
            blocks=("production",),
            observed_evidence="No completed Reviewer and Follow-up blind A/B artifact is registered.",
            required_action="Complete Reviewer and Follow-up blind A/B.",
            last_evaluated_at=timestamp,
        ),
        PromotionBlocker(
            code="SHADOW_NOT_AUTHORIZED",
            severity="hard_stop",
            blocks=("shadow", "canary", "production"),
            observed_evidence="Shadow is disabled and no authorization record is registered.",
            required_action="Obtain explicit Shadow authorization after all prerequisite gates pass.",
            last_evaluated_at=timestamp,
        ),
    )
    return PromotionDecision(allowed=False, blockers=blockers)
