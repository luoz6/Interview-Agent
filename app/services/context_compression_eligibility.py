from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from app.services.context_selection import ContextSelectionStats
from app.services.memory_metrics import MemoryMetricDimensions, MemoryMetricEvent, MemoryMetricValues, get_memory_metric_store


class CompressionEligibilityReason(StrEnum):
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
        get_memory_metric_store().publish(
            MemoryMetricEvent(
                metric_code="compression_eligibility",
                dimensions=MemoryMetricDimensions(
                    operation=(
                        "followup"
                        if target_artifact_type == "question_conversation"
                        else "evaluate"
                    ),
                    outcome="eligible" if result.eligible else "not_eligible",
                    reason=result.reason.value if result.reason else "none",
                    policy_version=result.policy_version,
                ),
                values=MemoryMetricValues(
                    source_count=result.source_unit_count,
                    dropped_count=result.dropped_count,
                    truncated_count=result.truncated_count,
                ),
            )
        )
        return result
