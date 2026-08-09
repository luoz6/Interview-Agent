from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


MemoryMetricCode = Literal[
    "context_route",
    "compression_eligibility",
    "provider_usage",
    "storage_snapshot",
    "deletion_outcome",
    "budget_shadow",
    "principal_read_shadow",
    "principal_local_consume",
]
MemoryRoute = Literal[
    "deterministic",
    "shadow_created",
    "artifact_created",
    "artifact_reused",
    "artifact_fallback",
    "memory_index_retrieved",
    "memory_index_empty",
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
        return {
            "schema_version": "memory-metrics-v1",
            "window_minutes": window_minutes,
            "observed_since": observed_since.isoformat(),
            "store_kind": self.store_kind,
            "data_complete": False,
            "latest_bucket_at": latest.isoformat() if latest else None,
            "items": items,
        }

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
            return result
        except ValueError:
            raise
        except Exception:
            self._primary_available = False
            result = self.fallback.aggregate(window_minutes=window_minutes)
            result["data_complete"] = False
            result["durable_store_kind"] = self.store_kind
            return result

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
) -> None:
    get_memory_metric_store().publish(
        MemoryMetricEvent(
            metric_code="provider_usage",
            dimensions=MemoryMetricDimensions(
                operation="provider",
                language_bucket=language_bucket,
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
