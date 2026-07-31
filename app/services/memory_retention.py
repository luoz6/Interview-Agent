from __future__ import annotations

from dataclasses import dataclass


class InMemorySessionCapacityExceeded(RuntimeError):
    """The in-memory runtime is full and has no finished session to evict."""


@dataclass(frozen=True)
class InMemorySessionRetentionPolicy:
    max_sessions: int = 1_000
    finished_ttl_seconds: int = 24 * 60 * 60
    cleanup_batch_size: int = 100

    def __post_init__(self) -> None:
        if self.max_sessions <= 0:
            raise ValueError("max_sessions must be positive")
        if self.finished_ttl_seconds <= 0:
            raise ValueError("finished_ttl_seconds must be positive")
        if self.cleanup_batch_size <= 0:
            raise ValueError("cleanup_batch_size must be positive")
