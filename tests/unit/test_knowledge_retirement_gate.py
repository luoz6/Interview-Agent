from app.domain.knowledge.retirement import (
    DataDrivenEnhancementEvidence,
    LegacyRetirementEvidence,
    LegacyRetirementStatus,
    eligible_data_driven_enhancements,
    evaluate_legacy_retirement,
)


def test_current_evidence_blocks_legacy_retirement():
    decision = evaluate_legacy_retirement(
        LegacyRetirementEvidence(
            runbook_updated=True,
            architecture_docs_updated=True,
        )
    )

    assert decision.status == LegacyRetirementStatus.BLOCKED
    assert "offline_eval_passed" in decision.missing_evidence
    assert "shadow_passed" in decision.missing_evidence
    assert "canary_passed" in decision.missing_evidence
    assert "rollback_exercised" in decision.missing_evidence


def test_retirement_requires_every_independent_gate():
    decision = evaluate_legacy_retirement(
        LegacyRetirementEvidence(
            offline_eval_passed=True,
            ablation_explains_gain=True,
            shadow_passed=True,
            canary_passed=True,
            evidence_replay_stable=True,
            no_critical_regression=True,
            rollback_exercised=True,
            runbook_updated=True,
            architecture_docs_updated=True,
            compatibility_removal_plan_updated=True,
        )
    )

    assert decision.status == LegacyRetirementStatus.ELIGIBLE
    assert decision.missing_evidence == ()


def test_no_data_means_no_speculative_enhancements():
    assert eligible_data_driven_enhancements(DataDrivenEnhancementEvidence()) == ()


def test_each_enhancement_requires_its_matching_evidence():
    enhancements = eligible_data_driven_enhancements(
        DataDrivenEnhancementEvidence(
            semantic_ranking_gap_proven=True,
            exact_term_recall_gap_proven=True,
        )
    )

    assert enhancements == (
        "remote_cross_encoder",
        "chinese_full_text_search",
        "pg_trgm",
    )
