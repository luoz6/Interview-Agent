from concurrent.futures import ThreadPoolExecutor
from typing import Any

from app.domain.runtime_events import RoundClosedEvent


def _unconfigured_round_review_payload_runner(payload: dict):
    raise RuntimeError("round review payload runner is not configured")


_default_round_review_payload_runner = _unconfigured_round_review_payload_runner


class NoopRuntimeEventPublisher:
    """Publisher boundary for intentionally disabled runtime events."""

    def publish(self, event: Any) -> None:
        return None

    def shutdown(self, *, wait: bool = True) -> None:
        return None


class LocalRoundReviewEventPublisher:
    """Local V1 async publisher for round review microbatches."""

    def __init__(self, *, executor=None, payload_runner=None) -> None:
        self._executor = executor or ThreadPoolExecutor(
            max_workers=2,
            thread_name_prefix="round-review",
        )
        self._payload_runner = payload_runner or _default_round_review_payload_runner

    def publish(self, event: Any) -> None:
        if isinstance(event, RoundClosedEvent):
            self._executor.submit(
                self._payload_runner,
                event.model_dump(mode="json"),
            )
            return None
        raise ValueError(f"unsupported runtime event: {type(event).__name__}")

    def shutdown(self, *, wait: bool = True) -> None:
        shutdown = getattr(self._executor, "shutdown", None)
        if shutdown is not None:
            shutdown(wait=wait)


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

    def shutdown(self, *, wait: bool = True) -> None:
        return None
