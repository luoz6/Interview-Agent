"""In-process and resilient adapters for memory metric storage."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone

from app.domain.memory.metrics import (
    MemoryMetricEvent,
    project_memory_metric_aggregate,
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
        return project_memory_metric_aggregate(
            {
                "schema_version": "memory-metrics-v1",
                "window_minutes": window_minutes,
                "observed_since": observed_since.isoformat(),
                "store_kind": self.store_kind,
                "data_complete": False,
                "latest_bucket_at": latest.isoformat() if latest else None,
                "items": items,
            }
        )

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
        self._events = [
            event for event in self._events if event.observed_at >= cutoff
        ]
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


__all__ = [
    "InMemoryMemoryMetricStore",
    "ResilientMemoryMetricStore",
    "UnavailableMemoryMetricStore",
]
