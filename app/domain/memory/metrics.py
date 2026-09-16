"""Content-free memory metric contracts and deterministic projections."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


MemoryMetricCode = Literal[
    "context_route",
    "compression_eligibility",
    "provider_usage",
    "storage_snapshot",
    "deletion_outcome",
    "budget_shadow",
    "principal_read_shadow",
    "principal_local_consume",
    "context_compression",
]
MemoryRoute = Literal[
    "deterministic",
    "shadow_created",
    "artifact_created",
    "artifact_reused",
    "artifact_fallback",
    "memory_index_retrieved",
    "memory_index_empty",
    "compression_eligible",
    "compression_bypassed",
    "provider_circuit_blocked",
    "validation_quarantine_blocked",
]
MemoryOperation = Literal[
    "prep",
    "followup",
    "evaluate",
    "report",
    "deletion",
    "storage",
    "provider",
]
MemoryLanguageBucket = Literal["zh_hans", "en", "mixed", "other", "unknown"]
MemoryOutcome = Literal[
    "eligible",
    "not_eligible",
    "completed",
    "failed",
    "insufficient_sample",
    "observing",
    "stopped",
]

CompressionWorkflow = Literal["interview", "review", "prep"]
CompressionMeasurementPath = Literal["business", "counterfactual"]
CompressionOperation = Literal[
    "question_conversation",
    "evidence_compression",
    "prep_context",
    "review_context",
]
CompressionTokenBucket = Literal[
    "unknown",
    "0",
    "1_256",
    "257_512",
    "513_1024",
    "1025_2048",
    "2049_4096",
    "4097_8192",
    "8193_16384",
    "16385_32768",
    "32769_plus",
]
CompressionRatioBucket = Literal[
    "unknown",
    "0_2500_bp",
    "2501_5000_bp",
    "5001_7500_bp",
    "7501_10000_bp",
    "10001_plus_bp",
]
CompressionLatencyBucket = Literal[
    "unknown",
    "0_99_ms",
    "100_499_ms",
    "500_999_ms",
    "1000_2499_ms",
    "2500_4999_ms",
    "5000_9999_ms",
    "10000_plus_ms",
]
CompressionEligibilityReason = Literal[
    "none",
    "below_threshold",
    "approaching_operation_budget",
    "older_complete_turn_would_drop",
    "older_complete_turn_excessively_truncated",
    "unresolved_topic_coverage_loss",
    "evidence_representation_excessive_truncation",
    "prep_section_coverage_loss",
    "review_continuity_would_drop",
]
CompressionValidationOutcome = Literal[
    "not_run",
    "valid",
    "invalid_json",
    "invalid_schema",
    "grounding_failed",
    "unsupported_excerpt",
    "numeric_literal_changed",
    "lease_lost",
    "unavailable",
]
CompressionFallbackOutcome = Literal[
    "not_used",
    "deterministic",
    "provider_failure",
    "validation_failure",
    "lease_loss",
    "circuit_blocked",
    "quarantine_blocked",
]
CompressionFailureState = Literal[
    "not_configured",
    "closed",
    "half_open",
    "open",
    "unavailable",
    "unknown",
]
CompressionFailureStoreOutcome = Literal[
    "not_configured",
    "not_queried",
    "available",
    "authorized",
    "blocked",
    "heartbeat_lost",
    "finish_committed",
    "abort_requested",
    "unavailable",
]
PrincipalLocalConsumeReason = Literal[
    "eligible",
    "no_eligible_fact",
    "state_changed",
    "token_cap",
    "current_candidate_missing",
    "runtime_failure",
]


class MemoryMetricDimensions(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    operation: MemoryOperation
    route: MemoryRoute | None = None
    outcome: MemoryOutcome | None = None
    reason: Literal[
        "approaching_operation_budget",
        "older_complete_turn_would_drop",
        "older_complete_turn_excessively_truncated",
        "unresolved_topic_coverage_loss",
        "evidence_representation_excessive_truncation",
        "prep_section_coverage_loss",
        "review_continuity_would_drop",
        "context_artifact_busy",
        "context_artifact_provider_failed",
        "context_artifact_validation_failed",
        "eligible",
        "no_eligible_fact",
        "state_changed",
        "token_cap",
        "current_candidate_missing",
        "runtime_failure",
        "none",
    ] | None = None
    policy_version: str | None = Field(
        default=None,
        pattern=r"^[a-z0-9][a-z0-9.-]{0,127}$",
    )
    schema_version: str | None = Field(
        default=None,
        pattern=r"^[a-z0-9][a-z0-9.-]{0,127}$",
    )
    language_bucket: MemoryLanguageBucket | None = None
    shadow_mode: bool = False
    consumption_enabled: bool = False
    workflow: CompressionWorkflow | None = None
    measurement_path: CompressionMeasurementPath | None = None
    intent_schema_version: str | None = Field(
        default=None,
        pattern=r"^[a-z0-9][a-z0-9.-]{0,127}$",
    )
    eligibility_reason: CompressionEligibilityReason | None = None
    source_token_bucket: CompressionTokenBucket | None = None
    target_token_bucket: CompressionTokenBucket | None = None
    result_token_bucket: CompressionTokenBucket | None = None
    compression_ratio_bucket: CompressionRatioBucket | None = None
    source_demand_token_bucket: CompressionTokenBucket | None = None
    duplicate_removed_token_bucket: CompressionTokenBucket | None = None
    post_dedup_demand_token_bucket: CompressionTokenBucket | None = None
    mandatory_bounded_raw_token_bucket: CompressionTokenBucket | None = None
    pre_dedup_required_token_bucket: CompressionTokenBucket | None = None
    post_dedup_required_token_bucket: CompressionTokenBucket | None = None
    business_pre_loss_required_token_bucket: CompressionTokenBucket | None = None
    shadow_post_dedup_required_token_bucket: CompressionTokenBucket | None = None
    business_utilization_basis_points: int | None = Field(
        default=None,
        ge=0,
        le=100_000,
    )
    shadow_post_dedup_utilization_basis_points: int | None = Field(
        default=None,
        ge=0,
        le=100_000,
    )
    estimator_error_basis_points: int | None = Field(
        default=None,
        ge=0,
        le=100_000,
    )
    exact_recent_preserved: bool | None = None
    current_answer_preserved: bool | None = None
    provider_usage_available: bool | None = None
    validation_outcome: CompressionValidationOutcome | None = None
    fallback_outcome: CompressionFallbackOutcome | None = None
    provider_circuit_state: CompressionFailureState | None = None
    validation_quarantine_state: CompressionFailureState | None = None
    failure_state_store_outcome: CompressionFailureStoreOutcome | None = None
    latency_bucket: CompressionLatencyBucket | None = None


class MemoryMetricValues(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    event_count: int = Field(default=1, ge=1)
    source_count: int = Field(default=0, ge=0)
    selected_count: int = Field(default=0, ge=0)
    dropped_count: int = Field(default=0, ge=0)
    truncated_count: int = Field(default=0, ge=0)
    estimated_input_tokens: int = Field(default=0, ge=0)
    provider_input_tokens: int = Field(default=0, ge=0)
    provider_output_tokens: int = Field(default=0, ge=0)
    latency_ms: int = Field(default=0, ge=0)
    attempts: int = Field(default=0, ge=0)
    size_bytes: int = Field(default=0, ge=0)
    queue_age_ms: int = Field(default=0, ge=0)
    active_count: int = Field(default=0, ge=0)
    superseded_count: int = Field(default=0, ge=0)
    referenced_count: int = Field(default=0, ge=0)
    orphan_count: int = Field(default=0, ge=0)


class MemoryMetricEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    metric_code: MemoryMetricCode
    dimensions: MemoryMetricDimensions
    values: MemoryMetricValues = Field(default_factory=MemoryMetricValues)
    observed_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


class CompressionObservation(BaseModel):
    """Strict, content-free input for bounded compression telemetry."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    measurement_path: CompressionMeasurementPath
    operation: CompressionOperation
    workflow: CompressionWorkflow
    policy_version: str = Field(pattern=r"^[a-z0-9][a-z0-9.-]{0,127}$")
    intent_schema_version: str = Field(
        pattern=r"^[a-z0-9][a-z0-9.-]{0,127}$"
    )
    eligibility_reason: CompressionEligibilityReason
    route: MemoryRoute
    source_token_bucket: CompressionTokenBucket
    target_token_bucket: CompressionTokenBucket
    result_token_bucket: CompressionTokenBucket
    compression_ratio_bucket: CompressionRatioBucket
    estimated_input_tokens: int = Field(ge=0)
    provider_input_tokens_when_available: int | None = Field(default=None, ge=0)
    provider_usage_available: bool
    estimator_error_basis_points: int = Field(ge=0, le=100_000)
    source_demand_token_bucket: CompressionTokenBucket
    duplicate_removed_token_bucket: CompressionTokenBucket
    post_dedup_demand_token_bucket: CompressionTokenBucket
    mandatory_bounded_raw_token_bucket: CompressionTokenBucket
    pre_dedup_required_token_bucket: CompressionTokenBucket
    post_dedup_required_token_bucket: CompressionTokenBucket
    business_pre_loss_required_token_bucket: CompressionTokenBucket
    shadow_post_dedup_required_token_bucket: CompressionTokenBucket
    business_utilization_basis_points: int | None = Field(
        default=None, ge=0, le=100_000
    )
    shadow_post_dedup_utilization_basis_points: int | None = Field(
        default=None, ge=0, le=100_000
    )
    selected_unit_count: int = Field(ge=0)
    dropped_unit_count: int = Field(ge=0)
    truncated_unit_count: int = Field(ge=0)
    deduplicated_unit_count: int = Field(ge=0)
    exact_recent_preserved: bool
    current_answer_preserved: bool
    validation_outcome: CompressionValidationOutcome
    fallback_outcome: CompressionFallbackOutcome
    provider_circuit_state: CompressionFailureState
    validation_quarantine_state: CompressionFailureState
    failure_state_store_outcome: CompressionFailureStoreOutcome
    latency_bucket: CompressionLatencyBucket
    language_bucket: MemoryLanguageBucket

    @model_validator(mode="after")
    def _validate_provider_usage(self) -> "CompressionObservation":
        actual = self.provider_input_tokens_when_available
        if self.provider_usage_available and actual is None:
            raise ValueError("available provider usage requires input tokens")
        if not self.provider_usage_available and actual is not None:
            raise ValueError("unavailable provider usage cannot include input tokens")
        return self


def project_memory_metric_aggregate(result: dict) -> dict:
    """Add stable logical aliases without changing persistence schemas."""

    for item in result.get("items", ()):
        dimensions = item.get("dimensions", {})
        values = item.get("values", {})
        if dimensions.get("provider_usage_available") is True:
            values["provider_input_tokens_when_available"] = values.get(
                "provider_input_tokens", 0
            )
        if item.get("metric_code") != "context_compression":
            continue
        values["selected_unit_count"] = values.get("selected_count", 0)
        values["dropped_unit_count"] = values.get("dropped_count", 0)
        values["truncated_unit_count"] = values.get("truncated_count", 0)
        values["deduplicated_unit_count"] = values.get("source_count", 0)
        values.setdefault("provider_input_tokens_when_available", None)
    return result


def compression_token_bucket(value: int | None) -> CompressionTokenBucket:
    if value is None:
        return "unknown"
    if value < 0:
        raise ValueError("token measurement must not be negative")
    boundaries = (
        (0, "0"),
        (256, "1_256"),
        (512, "257_512"),
        (1_024, "513_1024"),
        (2_048, "1025_2048"),
        (4_096, "2049_4096"),
        (8_192, "4097_8192"),
        (16_384, "8193_16384"),
        (32_768, "16385_32768"),
    )
    for upper, bucket in boundaries:
        if value <= upper:
            return bucket
    return "32769_plus"


def compression_ratio_bucket(
    *, source_tokens: int | None, result_tokens: int | None
) -> CompressionRatioBucket:
    if source_tokens is None or result_tokens is None or source_tokens <= 0:
        return "unknown"
    basis_points = result_tokens * 10_000 // source_tokens
    if basis_points <= 2_500:
        return "0_2500_bp"
    if basis_points <= 5_000:
        return "2501_5000_bp"
    if basis_points <= 7_500:
        return "5001_7500_bp"
    if basis_points <= 10_000:
        return "7501_10000_bp"
    return "10001_plus_bp"


def compression_latency_bucket(value_ms: int | None) -> CompressionLatencyBucket:
    if value_ms is None:
        return "unknown"
    if value_ms < 0:
        raise ValueError("latency must not be negative")
    boundaries = (
        (99, "0_99_ms"),
        (499, "100_499_ms"),
        (999, "500_999_ms"),
        (2_499, "1000_2499_ms"),
        (4_999, "2500_4999_ms"),
        (9_999, "5000_9999_ms"),
    )
    for upper, bucket in boundaries:
        if value_ms <= upper:
            return bucket
    return "10000_plus_ms"
