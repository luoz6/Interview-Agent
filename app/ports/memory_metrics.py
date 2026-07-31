from __future__ import annotations

from typing import Any, Protocol


class MemoryMetricStore(Protocol):
    """Privacy-safe aggregate metric store; no event or subject lookup API."""

    store_kind: str

    def publish(self, event: Any) -> None: ...

    def aggregate(self, *, window_minutes: int) -> dict: ...

    def rollup(self, *, batch_size: int = 1000) -> int: ...

    def cleanup(
        self,
        *,
        minute_retention_days: int = 30,
        hour_retention_days: int = 180,
        batch_size: int = 1000,
    ) -> dict[str, int]: ...

    def diagnostics(self) -> dict: ...
