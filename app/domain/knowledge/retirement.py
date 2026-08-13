from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class LegacyRetirementStatus(StrEnum):
    BLOCKED = "blocked"
    ELIGIBLE = "eligible"


class LegacyRetirementEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    offline_eval_passed: bool = False
    ablation_explains_gain: bool = False
    shadow_passed: bool = False
    canary_passed: bool = False
    evidence_replay_stable: bool = False
    no_critical_regression: bool = False
    rollback_exercised: bool = False
    runbook_updated: bool = False
    architecture_docs_updated: bool = False
    compatibility_removal_plan_updated: bool = False


class LegacyRetirementDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: LegacyRetirementStatus
    missing_evidence: tuple[str, ...] = ()
    decision_version: str = "legacy-retirement-gate-v1"


def evaluate_legacy_retirement(
    evidence: LegacyRetirementEvidence,
) -> LegacyRetirementDecision:
    missing = tuple(
        field_name
        for field_name in LegacyRetirementEvidence.model_fields
        if not getattr(evidence, field_name)
    )
    return LegacyRetirementDecision(
        status=(
            LegacyRetirementStatus.BLOCKED
            if missing
            else LegacyRetirementStatus.ELIGIBLE
        ),
        missing_evidence=missing,
    )


class DataDrivenEnhancementEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    exact_term_recall_gap_proven: bool = False
    semantic_ranking_gap_proven: bool = False
    taxonomy_routing_gap_proven: bool = False
    conflict_labeling_ready: bool = False


def eligible_data_driven_enhancements(
    evidence: DataDrivenEnhancementEvidence,
) -> tuple[str, ...]:
    result = []
    if evidence.semantic_ranking_gap_proven:
        result.append("remote_cross_encoder")
    if evidence.taxonomy_routing_gap_proven:
        result.extend(("taxonomy_v2", "knowledge_unit_schema_v2"))
    if evidence.exact_term_recall_gap_proven:
        result.extend(("chinese_full_text_search", "pg_trgm"))
    if evidence.conflict_labeling_ready:
        result.append("conflict_detection")
    return tuple(result)
