from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from app.services.context_selection import ContextSelectionStats
from app.services.memory_metrics import (
    CompressionObservation,
    MemoryMetricDimensions,
    MemoryMetricEvent,
    MemoryMetricValues,
    compression_token_bucket,
    get_memory_metric_store,
    publish_compression_observation,
)


class CompressionEligibilityReason(StrEnum):
    APPROACHING_OPERATION_BUDGET = "approaching_operation_budget"
    OLDER_COMPLETE_TURN_WOULD_DROP = "older_complete_turn_would_drop"
    OLDER_COMPLETE_TURN_EXCESSIVELY_TRUNCATED = (
        "older_complete_turn_excessively_truncated"
    )
    UNRESOLVED_TOPIC_COVERAGE_LOSS = "unresolved_topic_coverage_loss"
    EVIDENCE_REPRESENTATION_EXCESSIVE_TRUNCATION = (
        "evidence_representation_excessive_truncation"
    )
    PREP_SECTION_COVERAGE_LOSS = "prep_section_coverage_loss"
    REVIEW_CONTINUITY_WOULD_DROP = "review_continuity_would_drop"


CompressionEligibilityTarget = Literal[
    "question_conversation",
    "evidence_compression",
    "prep_context",
    "review_context",
]


@dataclass(frozen=True)
class ContextCompressionEligibility:
    eligible: bool
    reason: CompressionEligibilityReason | None
    source_unit_count: int
    dropped_count: int
    truncated_count: int
    target_artifact_type: CompressionEligibilityTarget
    policy_version: str
    source_manifest_sha256: str


class ContextCompressionEligibilityPolicy:
    policy_version = "context-compression-eligibility-v1"

    def __init__(
        self,
        *,
        eligibility_utilization_basis_points: int = 8_000,
    ) -> None:
        if not 1 <= eligibility_utilization_basis_points <= 10_000:
            raise ValueError(
                "eligibility_utilization_basis_points must be between 1 and 10000"
            )
        self.eligibility_utilization_basis_points = (
            eligibility_utilization_basis_points
        )

    def evaluate(
        self,
        *,
        selection_stats: ContextSelectionStats | None,
        target_artifact_type: CompressionEligibilityTarget,
        source_unit_count: int,
        source_manifest_sha256: str,
    ) -> ContextCompressionEligibility:
        dropped = 0
        truncated = 0
        reason = None
        if selection_stats is not None:
            if target_artifact_type == "evidence_compression":
                dropped = selection_stats.dropped_evidence_count
                truncated = selection_stats.truncated_evidence_count
                if dropped or truncated:
                    reason = (
                        CompressionEligibilityReason.EVIDENCE_REPRESENTATION_EXCESSIVE_TRUNCATION
                    )
            elif target_artifact_type == "question_conversation":
                dropped = selection_stats.dropped_message_count
                truncated = selection_stats.truncated_message_count
                if dropped:
                    reason = (
                        CompressionEligibilityReason.OLDER_COMPLETE_TURN_WOULD_DROP
                    )
                elif truncated:
                    reason = (
                        CompressionEligibilityReason.OLDER_COMPLETE_TURN_EXCESSIVELY_TRUNCATED
                    )
            elif target_artifact_type == "prep_context" and (
                selection_stats.dropped_message_count
                or selection_stats.truncated_message_count
            ):
                dropped = selection_stats.dropped_message_count
                truncated = selection_stats.truncated_message_count
                reason = CompressionEligibilityReason.PREP_SECTION_COVERAGE_LOSS
            elif target_artifact_type == "review_context" and (
                selection_stats.dropped_message_count
                or selection_stats.truncated_message_count
            ):
                dropped = selection_stats.dropped_message_count
                truncated = selection_stats.truncated_message_count
                reason = CompressionEligibilityReason.REVIEW_CONTINUITY_WOULD_DROP
            if (
                reason is None
                and target_artifact_type == "question_conversation"
                and selection_stats.business_pre_loss_required_tokens
                is not None
                and selection_stats.selectable_content_tokens is not None
                and selection_stats.selectable_content_tokens > 0
                and selection_stats.compressible_complete_history_unit_count
                is not None
                and selection_stats.compressible_complete_history_unit_count
                > 0
                and selection_stats.business_pre_loss_required_tokens * 10_000
                >= selection_stats.selectable_content_tokens
                * self.eligibility_utilization_basis_points
            ):
                reason = (
                    CompressionEligibilityReason.APPROACHING_OPERATION_BUDGET
                )
        result = ContextCompressionEligibility(
            eligible=reason is not None and source_unit_count > 0,
            reason=reason,
            source_unit_count=source_unit_count,
            dropped_count=dropped,
            truncated_count=truncated,
            target_artifact_type=target_artifact_type,
            policy_version=self.policy_version,
            source_manifest_sha256=source_manifest_sha256,
        )
        try:
            get_memory_metric_store().publish(
                MemoryMetricEvent(
                    metric_code="compression_eligibility",
                    dimensions=MemoryMetricDimensions(
                        operation=(
                            "followup"
                            if target_artifact_type == "question_conversation"
                            else "evaluate"
                        ),
                        outcome=(
                            "eligible" if result.eligible else "not_eligible"
                        ),
                        reason=(
                            result.reason.value if result.reason else "none"
                        ),
                        policy_version=result.policy_version,
                    ),
                    values=MemoryMetricValues(
                        source_count=result.source_unit_count,
                        dropped_count=result.dropped_count,
                        truncated_count=result.truncated_count,
                    ),
                )
            )
        except Exception:
            pass
        try:
            self._publish_observations(
                result=result,
                selection_stats=selection_stats,
            )
        except Exception:
            pass
        return result

    def _publish_observations(
        self,
        *,
        result: ContextCompressionEligibility,
        selection_stats: ContextSelectionStats | None,
    ) -> None:
        if selection_stats is None:
            return
        workflow = {
            "question_conversation": "interview",
            "evidence_compression": "interview",
            "prep_context": "prep",
            "review_context": "review",
        }[result.target_artifact_type]
        selected = (
            selection_stats.selected_evidence_count
            if result.target_artifact_type == "evidence_compression"
            else selection_stats.selected_message_count
        )
        common = {
            "operation": result.target_artifact_type,
            "workflow": workflow,
            "policy_version": result.policy_version,
            "intent_schema_version": "none",
            "eligibility_reason": (
                result.reason.value
                if result.reason is not None
                else "below_threshold"
            ),
            "route": (
                "compression_eligible" if result.eligible else "compression_bypassed"
            ),
            "target_token_bucket": "unknown",
            "result_token_bucket": "unknown",
            "compression_ratio_bucket": "unknown",
            "provider_input_tokens_when_available": None,
            "provider_usage_available": False,
            "estimator_error_basis_points": 0,
            "selected_unit_count": selected,
            "dropped_unit_count": result.dropped_count,
            "truncated_unit_count": result.truncated_count,
            "exact_recent_preserved": (
                selection_stats.exact_recent_truncated_message_count == 0
            ),
            "current_answer_preserved": True,
            "validation_outcome": "not_run",
            "fallback_outcome": (
                "not_used" if result.eligible else "deterministic"
            ),
            "provider_circuit_state": "not_configured",
            "validation_quarantine_state": "not_configured",
            "failure_state_store_outcome": "not_configured",
            "latency_bucket": "unknown",
            "language_bucket": "unknown",
        }
        business_tokens = selection_stats.business_pre_loss_required_tokens
        business = CompressionObservation(
            **common,
            measurement_path="business",
            source_token_bucket=compression_token_bucket(business_tokens),
            estimated_input_tokens=business_tokens or 0,
            source_demand_token_bucket=compression_token_bucket(
                selection_stats.source_demand_tokens
            ),
            duplicate_removed_token_bucket=compression_token_bucket(
                selection_stats.duplicate_removed_tokens
            ),
            post_dedup_demand_token_bucket=compression_token_bucket(
                selection_stats.post_dedup_demand_tokens
            ),
            mandatory_bounded_raw_token_bucket=compression_token_bucket(
                selection_stats.mandatory_bounded_raw_tokens
            ),
            pre_dedup_required_token_bucket=compression_token_bucket(
                selection_stats.pre_dedup_required_tokens
            ),
            post_dedup_required_token_bucket=compression_token_bucket(
                selection_stats.post_dedup_required_tokens
            ),
            business_pre_loss_required_token_bucket=compression_token_bucket(
                business_tokens
            ),
            shadow_post_dedup_required_token_bucket="unknown",
            business_utilization_basis_points=(
                min(
                    100_000,
                    selection_stats.business_utilization_basis_points,
                )
                if selection_stats.business_utilization_basis_points
                is not None
                else None
            ),
            shadow_post_dedup_utilization_basis_points=None,
            deduplicated_unit_count=selection_stats.deduplicated_unit_count,
        )
        publish_compression_observation(business)
        shadow_tokens = selection_stats.shadow_post_dedup_required_tokens
        if shadow_tokens is None:
            return
        selectable_tokens = selection_stats.selectable_content_tokens
        counterfactual_eligible = bool(
            selectable_tokens is not None
            and selectable_tokens > 0
            and selection_stats.compressible_complete_history_unit_count
            is not None
            and selection_stats.compressible_complete_history_unit_count > 0
            and shadow_tokens * 10_000
            >= selectable_tokens * self.eligibility_utilization_basis_points
        )
        counterfactual_reason = (
            CompressionEligibilityReason.APPROACHING_OPERATION_BUDGET.value
            if counterfactual_eligible
            else "below_threshold"
        )
        counterfactual = CompressionObservation(
            **{
                **common,
                "eligibility_reason": counterfactual_reason,
                "route": (
                    "compression_eligible"
                    if counterfactual_eligible
                    else "compression_bypassed"
                ),
                "fallback_outcome": (
                    "not_used"
                    if counterfactual_eligible
                    else "deterministic"
                ),
            },
            measurement_path="counterfactual",
            source_token_bucket=compression_token_bucket(shadow_tokens),
            estimated_input_tokens=shadow_tokens,
            source_demand_token_bucket=compression_token_bucket(
                selection_stats.source_demand_tokens
            ),
            duplicate_removed_token_bucket=compression_token_bucket(
                selection_stats.shadow_duplicate_removed_tokens
            ),
            post_dedup_demand_token_bucket=compression_token_bucket(shadow_tokens),
            mandatory_bounded_raw_token_bucket=compression_token_bucket(
                selection_stats.mandatory_bounded_raw_tokens
            ),
            pre_dedup_required_token_bucket=compression_token_bucket(
                selection_stats.pre_dedup_required_tokens
            ),
            post_dedup_required_token_bucket=compression_token_bucket(shadow_tokens),
            business_pre_loss_required_token_bucket="unknown",
            shadow_post_dedup_required_token_bucket=compression_token_bucket(
                shadow_tokens
            ),
            business_utilization_basis_points=None,
            shadow_post_dedup_utilization_basis_points=(
                min(
                    100_000,
                    selection_stats.shadow_post_dedup_utilization_basis_points,
                )
                if selection_stats.shadow_post_dedup_utilization_basis_points
                is not None
                else None
            ),
            deduplicated_unit_count=(
                selection_stats.shadow_deduplicated_unit_count
            ),
        )
        publish_compression_observation(counterfactual)
