from typing import Any

from app.services.runtime_domain_events import RoundClosedEvent


class NoopRuntimeEventPublisher:
    """Local V1 publisher boundary for future event fanout adapters."""

    def publish(self, event: Any) -> None:
        return None


class CeleryRuntimeEventPublisher:
    def __init__(self, *, celery_app) -> None:
        self._celery_app = celery_app

    def publish(self, event: Any) -> None:
        if isinstance(event, RoundClosedEvent):
            self._celery_app.send_task(
                "app.services.round_review_tasks.run_closed_round_review",
                args=[event.model_dump(mode="json")],
            )
            return None
        raise ValueError(f"unsupported runtime event: {type(event).__name__}")
