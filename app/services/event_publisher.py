from typing import Any


class NoopRuntimeEventPublisher:
    """Local V1 publisher boundary for future event fanout adapters."""

    def publish(self, event: Any) -> None:
        return None
