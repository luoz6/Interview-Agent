"""Runtime wiring and publishers for content-free memory telemetry."""

from __future__ import annotations

from typing import Literal

from app.adapters.memory.memory_metrics import (
    InMemoryMemoryMetricStore,
    ResilientMemoryMetricStore,
    UnavailableMemoryMetricStore,
)
from app.domain.memory.metrics import (
    CompressionEligibilityReason,
    CompressionFailureState,
    CompressionFailureStoreOutcome,
    CompressionFallbackOutcome,
    CompressionLatencyBucket,
    CompressionMeasurementPath,
    CompressionObservation,
    CompressionOperation,
    CompressionRatioBucket,
    CompressionTokenBucket,
    CompressionValidationOutcome,
    CompressionWorkflow,
    MemoryLanguageBucket,
    MemoryMetricCode,
    MemoryMetricDimensions,
    MemoryMetricEvent,
    MemoryMetricValues,
    MemoryOperation,
    MemoryOutcome,
    MemoryRoute,
    PrincipalLocalConsumeReason,
    compression_latency_bucket,
    compression_ratio_bucket,
    compression_token_bucket,
    project_memory_metric_aggregate,
)


_process_local_memory_metric_store = InMemoryMemoryMetricStore()
_memory_metric_store = _process_local_memory_metric_store


def get_memory_metric_store():
    return _memory_metric_store


def configure_memory_metric_store(store) -> None:
    global _memory_metric_store
    _memory_metric_store = store


def reset_memory_metric_store() -> None:
    global _memory_metric_store
    _process_local_memory_metric_store.clear()
    _memory_metric_store = _process_local_memory_metric_store


def publish_memory_metric_event(event: MemoryMetricEvent | dict) -> None:
    get_memory_metric_store().publish(MemoryMetricEvent.model_validate(event))


def publish_compression_observation(
    observation: CompressionObservation | dict,
) -> None:
    """Publish one bounded observation; telemetry failures are non-authoritative."""

    validated = CompressionObservation.model_validate(observation)
    operation: MemoryOperation = {
        "question_conversation": "followup",
        "evidence_compression": "evaluate",
        "prep_context": "prep",
        "review_context": "report",
    }[validated.operation]
    dimensions = MemoryMetricDimensions(
        operation=operation,
        route=validated.route,
        policy_version=validated.policy_version,
        language_bucket=validated.language_bucket,
        shadow_mode=validated.measurement_path == "counterfactual",
        consumption_enabled=validated.measurement_path == "business",
        workflow=validated.workflow,
        measurement_path=validated.measurement_path,
        intent_schema_version=validated.intent_schema_version,
        eligibility_reason=validated.eligibility_reason,
        source_token_bucket=validated.source_token_bucket,
        target_token_bucket=validated.target_token_bucket,
        result_token_bucket=validated.result_token_bucket,
        compression_ratio_bucket=validated.compression_ratio_bucket,
        source_demand_token_bucket=validated.source_demand_token_bucket,
        duplicate_removed_token_bucket=validated.duplicate_removed_token_bucket,
        post_dedup_demand_token_bucket=validated.post_dedup_demand_token_bucket,
        mandatory_bounded_raw_token_bucket=(
            validated.mandatory_bounded_raw_token_bucket
        ),
        pre_dedup_required_token_bucket=(
            validated.pre_dedup_required_token_bucket
        ),
        post_dedup_required_token_bucket=(
            validated.post_dedup_required_token_bucket
        ),
        business_pre_loss_required_token_bucket=(
            validated.business_pre_loss_required_token_bucket
        ),
        shadow_post_dedup_required_token_bucket=(
            validated.shadow_post_dedup_required_token_bucket
        ),
        business_utilization_basis_points=(
            validated.business_utilization_basis_points
        ),
        shadow_post_dedup_utilization_basis_points=(
            validated.shadow_post_dedup_utilization_basis_points
        ),
        estimator_error_basis_points=validated.estimator_error_basis_points,
        exact_recent_preserved=validated.exact_recent_preserved,
        current_answer_preserved=validated.current_answer_preserved,
        provider_usage_available=validated.provider_usage_available,
        validation_outcome=validated.validation_outcome,
        fallback_outcome=validated.fallback_outcome,
        provider_circuit_state=validated.provider_circuit_state,
        validation_quarantine_state=validated.validation_quarantine_state,
        failure_state_store_outcome=validated.failure_state_store_outcome,
        latency_bucket=validated.latency_bucket,
    )
    event = MemoryMetricEvent(
        metric_code="context_compression",
        dimensions=dimensions,
        values=MemoryMetricValues(
            source_count=validated.deduplicated_unit_count,
            selected_count=validated.selected_unit_count,
            dropped_count=validated.dropped_unit_count,
            truncated_count=validated.truncated_unit_count,
            estimated_input_tokens=validated.estimated_input_tokens,
            provider_input_tokens=(
                validated.provider_input_tokens_when_available or 0
            ),
        ),
    )
    try:
        publish_memory_metric_event(event)
    except Exception:
        return


def publish_memory_route(
    *,
    operation: MemoryOperation,
    route: MemoryRoute,
    policy_version: str | None = None,
    source_count: int = 0,
) -> None:
    publish_memory_metric_event(
        MemoryMetricEvent(
            metric_code="context_route",
            dimensions=MemoryMetricDimensions(
                operation=operation,
                route=route,
                policy_version=policy_version,
            ),
            values=MemoryMetricValues(source_count=source_count),
        )
    )


def publish_deletion_outcome(
    *, outcome: Literal["completed", "failed"], attempts: int, latency_ms: int
) -> None:
    publish_memory_metric_event(
        MemoryMetricEvent(
            metric_code="deletion_outcome",
            dimensions=MemoryMetricDimensions(
                operation="deletion",
                outcome=outcome,
            ),
            values=MemoryMetricValues(
                attempts=attempts,
                latency_ms=latency_ms,
            ),
        )
    )


def publish_provider_usage_metric(
    *,
    language_bucket: MemoryLanguageBucket,
    estimated_input_tokens: int,
    provider_input_tokens: int,
    provider_output_tokens: int,
    estimator_error_basis_points: int = 0,
    operation: MemoryOperation = "provider",
    workflow: CompressionWorkflow | None = None,
    policy_version: str | None = None,
    intent_schema_version: str | None = None,
    measurement_path: CompressionMeasurementPath | None = None,
) -> None:
    publish_memory_metric_event(
        MemoryMetricEvent(
            metric_code="provider_usage",
            dimensions=MemoryMetricDimensions(
                operation=operation,
                language_bucket=language_bucket,
                workflow=workflow,
                policy_version=policy_version,
                intent_schema_version=intent_schema_version,
                measurement_path=measurement_path,
                provider_usage_available=True,
                estimator_error_basis_points=estimator_error_basis_points,
            ),
            values=MemoryMetricValues(
                estimated_input_tokens=estimated_input_tokens,
                provider_input_tokens=provider_input_tokens,
                provider_output_tokens=provider_output_tokens,
            ),
        )
    )


def publish_budget_shadow_metric(
    *,
    operation: MemoryOperation,
    outcome: Literal["completed", "failed", "observing", "stopped"],
    language_bucket: MemoryLanguageBucket,
    source_count: int,
    selected_count: int,
    dropped_count: int,
    estimated_input_tokens: int,
    latency_ms: int = 0,
) -> None:
    publish_memory_metric_event(
        MemoryMetricEvent(
            metric_code="budget_shadow",
            dimensions=MemoryMetricDimensions(
                operation=operation,
                outcome=outcome,
                language_bucket=language_bucket,
                shadow_mode=True,
                consumption_enabled=False,
            ),
            values=MemoryMetricValues(
                source_count=source_count,
                selected_count=selected_count,
                dropped_count=dropped_count,
                estimated_input_tokens=estimated_input_tokens,
                latency_ms=latency_ms,
            ),
        )
    )


def publish_principal_read_shadow_metric(
    *,
    outcome: Literal["completed", "failed"],
    source_count: int,
    selected_count: int,
    dropped_count: int,
    estimated_input_tokens: int,
    latency_ms: int,
) -> None:
    publish_memory_metric_event(
        MemoryMetricEvent(
            metric_code="principal_read_shadow",
            dimensions=MemoryMetricDimensions(
                operation="followup",
                outcome=outcome,
                shadow_mode=True,
                consumption_enabled=False,
            ),
            values=MemoryMetricValues(
                source_count=source_count,
                selected_count=selected_count,
                dropped_count=dropped_count,
                estimated_input_tokens=estimated_input_tokens,
                latency_ms=latency_ms,
            ),
        )
    )


def publish_principal_local_consume_metric(
    *,
    outcome: Literal["consumed", "suppressed", "failed"],
    reason: PrincipalLocalConsumeReason,
    selected_count: int,
    estimated_input_tokens: int,
) -> None:
    """Publish aggregate-only Local Consume telemetry."""

    metric_outcome: Literal["completed", "failed", "stopped"] = {
        "consumed": "completed",
        "suppressed": "stopped",
        "failed": "failed",
    }[outcome]
    publish_memory_metric_event(
        MemoryMetricEvent(
            metric_code="principal_local_consume",
            dimensions=MemoryMetricDimensions(
                operation="followup",
                outcome=metric_outcome,
                reason=reason,
                shadow_mode=False,
                consumption_enabled=True,
            ),
            values=MemoryMetricValues(
                selected_count=selected_count,
                estimated_input_tokens=estimated_input_tokens,
            ),
        )
    )


__all__ = [
    "CompressionEligibilityReason",
    "CompressionFailureState",
    "CompressionFailureStoreOutcome",
    "CompressionFallbackOutcome",
    "CompressionLatencyBucket",
    "CompressionMeasurementPath",
    "CompressionObservation",
    "CompressionOperation",
    "CompressionRatioBucket",
    "CompressionTokenBucket",
    "CompressionValidationOutcome",
    "CompressionWorkflow",
    "InMemoryMemoryMetricStore",
    "MemoryLanguageBucket",
    "MemoryMetricCode",
    "MemoryMetricDimensions",
    "MemoryMetricEvent",
    "MemoryMetricValues",
    "MemoryOperation",
    "MemoryOutcome",
    "MemoryRoute",
    "PrincipalLocalConsumeReason",
    "ResilientMemoryMetricStore",
    "UnavailableMemoryMetricStore",
    "compression_latency_bucket",
    "compression_ratio_bucket",
    "compression_token_bucket",
    "configure_memory_metric_store",
    "get_memory_metric_store",
    "project_memory_metric_aggregate",
    "publish_budget_shadow_metric",
    "publish_compression_observation",
    "publish_deletion_outcome",
    "publish_memory_metric_event",
    "publish_memory_route",
    "publish_principal_local_consume_metric",
    "publish_principal_read_shadow_metric",
    "publish_provider_usage_metric",
    "reset_memory_metric_store",
]
