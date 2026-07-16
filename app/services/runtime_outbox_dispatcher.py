from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from threading import Event, Lock, Thread
from typing import Any

from app.services.runtime_work import (
    classify_runtime_failure,
    retry_delay_seconds,
)


logger = logging.getLogger(__name__)


class RuntimeOutboxDispatcher:
    def __init__(
        self,
        repository,
        sink,
        *,
        batch_size: int = 20,
        lease_seconds: int = 60,
    ) -> None:
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        if lease_seconds < 1:
            raise ValueError("lease_seconds must be positive")
        self.repository = repository
        self.sink = sink
        self.batch_size = batch_size
        self.lease_seconds = lease_seconds

    def run_once(self, worker_id: str) -> int:
        claims = self.repository.claim_batch(
            worker_id=worker_id,
            limit=self.batch_size,
            lease_seconds=self.lease_seconds,
        )
        for claim in claims:
            event_id = claim["event_id"]
            try:
                self.sink.publish(claim["payload"])
            except Exception as exc:
                failure = classify_runtime_failure(exc)
                if (
                    not failure.retryable
                    or claim["attempt_count"] >= claim["max_attempts"]
                ):
                    self.repository.mark_dead_letter(
                        event_id,
                        worker_id,
                        error_code=failure.code,
                    )
                    continue
                delay = retry_delay_seconds(claim["attempt_count"])
                self.repository.mark_retrying(
                    event_id,
                    worker_id,
                    error_code=failure.code,
                    available_at=(
                        datetime.now(timezone.utc)
                        + timedelta(seconds=delay)
                    ),
                )
            else:
                self.repository.mark_published(event_id, worker_id)
        return len(claims)


class RuntimeOutboxService:
    def __init__(
        self,
        dispatcher: RuntimeOutboxDispatcher,
        *,
        worker_id: str,
        poll_seconds: float = 0.5,
    ) -> None:
        if not worker_id:
            raise ValueError("worker_id is required")
        if poll_seconds <= 0:
            raise ValueError("poll_seconds must be positive")
        self.dispatcher = dispatcher
        self.worker_id = worker_id
        self.poll_seconds = poll_seconds
        self._stop_event = Event()
        self._lock = Lock()
        self._thread: Thread | None = None

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = Thread(
                target=self.run_forever,
                name=f"runtime-outbox-{self.worker_id}",
                daemon=True,
            )
            self._thread.start()

    def run_forever(self) -> None:
        while not self._stop_event.is_set():
            try:
                processed = self.dispatcher.run_once(self.worker_id)
            except Exception:
                logger.warning(
                    "runtime outbox dispatch cycle failed",
                    extra={"error_code": "outbox_repository_unavailable"},
                )
                processed = 0
            if processed == 0:
                self._stop_event.wait(self.poll_seconds)

    def shutdown(self, *, wait: bool = True) -> None:
        self._stop_event.set()
        thread = self._thread
        if wait and thread is not None:
            thread.join()


class LocalRuntimeEventSink:
    def __init__(self, *, control_store, worker_id: str) -> None:
        self.control_store = control_store
        self.worker_id = worker_id

    def publish(self, payload: dict[str, Any]) -> None:
        from app.services.runtime_event_consumer import (
            consume_round_review_event_payload,
        )

        consume_round_review_event_payload(
            payload,
            control_store=self.control_store,
            worker_id=self.worker_id,
        )


class CeleryRuntimeEventSink:
    task_name = (
        "app.services.round_review_tasks.run_closed_round_review"
    )

    def __init__(self, *, celery_app) -> None:
        self.celery_app = celery_app

    def publish(self, payload: dict[str, Any]) -> None:
        self.celery_app.send_task(self.task_name, args=[payload])
