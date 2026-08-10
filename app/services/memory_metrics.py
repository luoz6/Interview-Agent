from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
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


class InMemoryMemoryMetricStore:
    store_kind = "process_local"

    def __init__(self, *, clock=None, minimum_language_samples: int = 5):
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.minimum_language_samples = minimum_language_samples
        self._events: list[MemoryMetricEvent] = []

    def publish(self, event: MemoryMetricEvent | dict) -> None:
        validated = MemoryMetricEvent.model_validate(event)
        if validated.observed_at.tzinfo is None:
            raise ValueError("memory metric timestamp must be timezone-aware")
        self._events.append(validated)

    def aggregate(self, *, window_minutes: int) -> dict:
        if window_minutes not in {15, 60, 360, 1440}:
            raise ValueError("unsupported memory metrics window")
        observed_since = self.clock() - timedelta(minutes=window_minutes)
        grouped = defaultdict(lambda: defaultdict(int))
        for event in self._events:
            if event.observed_at < observed_since:
                continue
            dimensions = event.dimensions.model_dump(exclude_none=True)
            key = (
                event.metric_code,
                tuple(sorted(dimensions.items())),
            )
            for name, value in event.values.model_dump().items():
                grouped[key][name] += value
        items = []
        for (metric_code, dimension_items), values in sorted(
            grouped.items(), key=lambda item: repr(item[0])
        ):
            dimensions = dict(dimension_items)
            sample_status = "sufficient"
            if (
                metric_code == "provider_usage"
                and values["event_count"] < self.minimum_language_samples
            ):
                sample_status = "insufficient_sample"
            items.append(
                {
                    "metric_code": metric_code,
                    "dimensions": dimensions,
                    "values": dict(values),
                    "sample_status": sample_status,
                }
            )
        latest = max(
            (
                event.observed_at
                for event in self._events
                if event.observed_at >= observed_since
            ),
            default=None,
        )
        return project_memory_metric_aggregate({
            "schema_version": "memory-metrics-v1",
            "window_minutes": window_minutes,
            "observed_since": observed_since.isoformat(),
            "store_kind": self.store_kind,
            "data_complete": False,
            "latest_bucket_at": latest.isoformat() if latest else None,
            "items": items,
        })

    def rollup(self, *, batch_size: int = 1000) -> int:
        if batch_size < 1:
            raise ValueError("memory metric batch size must be positive")
        return 0

    def cleanup(
        self,
        *,
        minute_retention_days: int = 30,
        hour_retention_days: int = 180,
        batch_size: int = 1000,
    ) -> dict[str, int]:
        if min(minute_retention_days, hour_retention_days, batch_size) < 1:
            raise ValueError("memory metric retention values must be positive")
        cutoff = self.clock() - timedelta(days=minute_retention_days)
        before = len(self._events)
        self._events = [event for event in self._events if event.observed_at >= cutoff]
        return {"minute_deleted": before - len(self._events), "hour_deleted": 0}

    def diagnostics(self) -> dict:
        latest = max((event.observed_at for event in self._events), default=None)
        return {
            "store_kind": self.store_kind,
            "data_complete": False,
            "latest_bucket_at": latest.isoformat() if latest else None,
        }

    def clear(self) -> None:
        self._events.clear()


class ResilientMemoryMetricStore:
    """Fail-open durable metrics with an explicitly incomplete local fallback."""

    store_kind = "postgres_aggregate"

    def __init__(self, *, primary, fallback=None):
        self.primary = primary
        self.fallback = fallback or InMemoryMemoryMetricStore()
        self._primary_available = True

    def publish(self, event: MemoryMetricEvent | dict) -> None:
        validated = MemoryMetricEvent.model_validate(event)
        try:
            self.primary.publish(validated)
            self._primary_available = True
        except Exception:
            self._primary_available = False
        finally:
            self.fallback.publish(validated)

    def aggregate(self, *, window_minutes: int) -> dict:
        try:
            result = self.primary.aggregate(window_minutes=window_minutes)
            self._primary_available = True
            return project_memory_metric_aggregate(result)
        except ValueError:
            raise
        except Exception:
            self._primary_available = False
            result = self.fallback.aggregate(window_minutes=window_minutes)
            result["data_complete"] = False
            result["durable_store_kind"] = self.store_kind
            return project_memory_metric_aggregate(result)

    def rollup(self, *, batch_size: int = 1000) -> int:
        try:
            result = self.primary.rollup(batch_size=batch_size)
            self._primary_available = True
            return result
        except Exception:
            self._primary_available = False
            return 0

    def cleanup(self, **kwargs) -> dict[str, int]:
        try:
            result = self.primary.cleanup(**kwargs)
            self._primary_available = True
            return result
        except Exception:
            self._primary_available = False
            return {"minute_deleted": 0, "hour_deleted": 0}

    def diagnostics(self) -> dict:
        if self._primary_available:
            try:
                return self.primary.diagnostics()
            except Exception:
                self._primary_available = False
        local = self.fallback.diagnostics()
        return {
            **local,
            "data_complete": False,
            "durable_store_kind": self.store_kind,
        }


class UnavailableMemoryMetricStore:
    store_kind = "postgres_aggregate"

    @staticmethod
    def _raise():
        raise RuntimeError("durable memory metrics are unavailable")

    def publish(self, event) -> None:
        self._raise()

    def aggregate(self, *, window_minutes: int) -> dict:
        self._raise()

    def rollup(self, *, batch_size: int = 1000) -> int:
        self._raise()

    def cleanup(self, **kwargs) -> dict[str, int]:
        self._raise()

    def diagnostics(self) -> dict:
        self._raise()


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


def project_memory_metric_aggregate(result: dict) -> dict:
    """Add stable logical aliases without changing the PostgreSQL schema."""

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
        get_memory_metric_store().publish(event)
    except Exception:
        return


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


def publish_memory_route(
    *,
    operation: MemoryOperation,
    route: MemoryRoute,
    policy_version: str | None = None,
    source_count: int = 0,
) -> None:
    get_memory_metric_store().publish(
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
    get_memory_metric_store().publish(
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
    get_memory_metric_store().publish(
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
    get_memory_metric_store().publish(
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
    *, outcome: Literal["completed", "failed"], source_count: int,
    selected_count: int, dropped_count: int, estimated_input_tokens: int,
    latency_ms: int,
) -> None:
    get_memory_metric_store().publish(
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
    """Publish aggregate-only Local Consume telemetry.

    The schema deliberately accepts no principal, session, fact, source,
    prompt, answer, or free-text fields.
    """

    metric_outcome: Literal["completed", "failed", "stopped"] = {
        "consumed": "completed",
        "suppressed": "stopped",
        "failed": "failed",
    }[outcome]
    get_memory_metric_store().publish(
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
