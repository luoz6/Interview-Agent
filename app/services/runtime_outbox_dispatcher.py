from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from threading import Event, Lock, Thread
from typing import Any

from app.services.runtime_work import (
    classify_runtime_failure,
    retry_delay_seconds,
)
from app.services.runtime_signal_metrics import CANARY_SIGNAL_CODES


logger = logging.getLogger(__name__)


class RuntimeOutboxDispatcher:
    def __init__(
        self,
        repository,
        sink,
        *,
        batch_size: int = 20,
        lease_seconds: int = 60,
        heartbeat_seconds: float | None = None,
        signal_store=None,
    ) -> None:
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        if lease_seconds < 1:
            raise ValueError("lease_seconds must be positive")
        self.repository = repository
        self.sink = sink
        self.batch_size = batch_size
        self.lease_seconds = lease_seconds
        self.signal_store = signal_store
        self.heartbeat_seconds = (
            max(0.1, lease_seconds / 3)
            if heartbeat_seconds is None
            else heartbeat_seconds
        )

    def run_once(self, worker_id: str) -> int:
        claims = self.repository.claim_batch(
            worker_id=worker_id,
            limit=self.batch_size,
            lease_seconds=self.lease_seconds,
        )
        if not claims:
            return 0
        active_event_ids = {claim["event_id"] for claim in claims}
        active_lock = Lock()
        heartbeat_stop = Event()
        lease_lost = Event()
        heartbeat = Thread(
            target=self._heartbeat_leases,
            args=(
                heartbeat_stop,
                lease_lost,
                active_event_ids,
                active_lock,
                worker_id,
            ),
            daemon=True,
        )
        heartbeat.start()
        processed = 0
        try:
            for claim in claims:
                if lease_lost.is_set():
                    break
                event_id = claim["event_id"]
                try:
                    self.sink.publish(claim["payload"])
                    if lease_lost.is_set():
                        raise RuntimeError("outbox_lease_lost")
                except Exception as exc:
                    failure = classify_runtime_failure(exc)
                    self._record_signal(claim["payload"], failure.code)
                    with active_lock:
                        if (
                            not failure.retryable
                            or claim["attempt_count"]
                            >= claim["max_attempts"]
                        ):
                            self.repository.mark_dead_letter(
                                event_id,
                                worker_id,
                                error_code=failure.code,
                            )
                        else:
                            delay = retry_delay_seconds(
                                claim["attempt_count"]
                            )
                            self.repository.mark_retrying(
                                event_id,
                                worker_id,
                                error_code=failure.code,
                                available_at=(
                                    datetime.now(timezone.utc)
                                    + timedelta(seconds=delay)
                                ),
                            )
                        active_event_ids.discard(event_id)
                else:
                    with active_lock:
                        self.repository.mark_published(event_id, worker_id)
                        active_event_ids.discard(event_id)
                processed += 1
        finally:
            heartbeat_stop.set()
            heartbeat.join()
        return processed

    def _record_signal(self, payload: dict[str, Any], code: str) -> None:
        if self.signal_store is None or code not in CANARY_SIGNAL_CODES:
            return
        event_type = str(payload.get("event_type", ""))
        if event_type.startswith("interview_"):
            workflow_type = "interview"
        elif event_type == "review_retry_due":
            workflow_type = "review"
        else:
            return
        try:
            self.signal_store.increment(
                workflow_type=workflow_type,
                signal_code=code,
            )
        except Exception:
            logger.warning(
                "runtime canary signal write failed",
                extra={"error_code": "canary_signal_write_failed"},
            )

    def _heartbeat_leases(
        self,
        stop: Event,
        lease_lost: Event,
        active_event_ids: set[str],
        active_lock: Lock,
        worker_id: str,
    ) -> None:
        while not stop.wait(self.heartbeat_seconds):
            with active_lock:
                event_ids = list(active_event_ids)
                if not event_ids:
                    return
                extend_many = getattr(
                    self.repository, "extend_outbox_leases", None
                )
                if extend_many is not None:
                    extended = extend_many(
                        event_ids,
                        worker_id,
                        self.lease_seconds,
                    )
                else:
                    extend_one = getattr(
                        self.repository, "extend_outbox_lease", None
                    )
                    if extend_one is None:
                        return
                    extended = all(
                        extend_one(
                            event_id,
                            worker_id,
                            self.lease_seconds,
                        )
                        for event_id in event_ids
                    )
                if not extended:
                    lease_lost.set()
                    return


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
    def __init__(
        self,
        *,
        control_store,
        worker_id: str,
        store=None,
        interview_consumer=None,
        review_consumer=None,
        principal_memory_consumer=None,
    ) -> None:
        self.control_store = control_store
        self.worker_id = worker_id
        self.store = store
        self.interview_consumer = interview_consumer
        self.review_consumer = review_consumer
        self.principal_memory_consumer = principal_memory_consumer

    def publish(self, payload: dict[str, Any]) -> None:
        if payload["event_type"] == "principal_memory_proposal_requested_v1":
            if self.principal_memory_consumer is None:
                raise RuntimeError("principal memory proposal consumer is unavailable")
            self.principal_memory_consumer.consume(payload)
            return
        if payload["event_type"] in {
            "interview_command_ready",
            "interview_retry_due",
        }:
            if self.interview_consumer is None:
                raise RuntimeError("interview workflow consumer is unavailable")
            self.interview_consumer.consume(payload)
            return
        if payload["event_type"] == "review_retry_due":
            if self.review_consumer is None:
                raise RuntimeError("review workflow consumer is unavailable")
            self.review_consumer.consume(payload)
            return
        from app.services.runtime_event_consumer import (
            consume_round_review_event_payload,
        )

        outcome = consume_round_review_event_payload(
            payload,
            control_store=self.control_store,
            worker_id=self.worker_id,
            store=self.store,
        )
        if outcome.status == "reschedule":
            raise RuntimeError(
                outcome.error_code or "runtime_work_retry"
            )


class CeleryRuntimeEventSink:
    task_name = (
        "app.services.round_review_tasks.run_closed_round_review"
    )

    def __init__(self, *, celery_app) -> None:
        self.celery_app = celery_app

    def publish(self, payload: dict[str, Any]) -> None:
        task_name = self.task_name
        if payload["event_type"] in {
            "interview_command_ready",
            "interview_retry_due",
        }:
            task_name = (
                "app.services.interview_workflow_tasks."
                "run_interview_workflow_event"
            )
        elif payload["event_type"] == "review_retry_due":
            task_name = (
                "app.services.review_workflow_tasks."
                "run_review_workflow_event"
            )
        elif payload["event_type"] == "principal_memory_proposal_requested_v1":
            task_name = (
                "app.services.principal_memory_tasks."
                "run_principal_memory_proposal_event"
            )
        self.celery_app.send_task(task_name, args=[payload])
