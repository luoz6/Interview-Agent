from datetime import datetime, timezone

from app.application.knowledge.promotion_service import (
    current_knowledge_promotion_decision,
)


def test_current_promotion_decision_is_versioned_blocked_and_complete():
    evaluated_at = datetime(2026, 8, 13, tzinfo=timezone.utc)
    decision = current_knowledge_promotion_decision(evaluated_at=evaluated_at)

    assert decision.allowed is False
    assert decision.decision_version == "knowledge-promotion-decision-v1"
    assert {item.code for item in decision.blockers} == {
        "HUMAN_TUNING_GT_MISSING",
        "NO_EVIDENCE_GATE_FAILED",
        "HYBRID_NOT_BETTER_THAN_LEGACY",
        "SEALED_HOLDOUT_MISSING",
        "BUSINESS_BLIND_AB_PENDING",
        "SHADOW_NOT_AUTHORIZED",
    }
    assert all(item.observed_evidence for item in decision.blockers)
    assert all(item.required_action for item in decision.blockers)
    assert all(item.last_evaluated_at == evaluated_at for item in decision.blockers)
