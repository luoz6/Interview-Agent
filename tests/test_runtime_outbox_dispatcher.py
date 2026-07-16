from dataclasses import dataclass
from datetime import datetime, timezone

from app.services.runtime_outbox_dispatcher import RuntimeOutboxDispatcher


def make_claim(
    event_id: str,
    *,
    attempt_count: int = 1,
    max_attempts: int = 5,
) -> dict:
    return {
        "event_id": event_id,
        "payload": {"event_id": event_id, "event_type": "round_closed"},
        "attempt_count": attempt_count,
        "max_attempts": max_attempts,
    }


@dataclass(frozen=True)
class RetryCall:
    event_id: str
    worker_id: str
    error_code: str
    delay_seconds: int


class FakeRepository:
    def __init__(self, claims):
        self.claims = claims
        self.published = []
        self.retried = []
        self.dead_lettered = []

    def claim_batch(self, *, worker_id, limit, lease_seconds):
        self.claim_args = (worker_id, limit, lease_seconds)
        return self.claims

    def mark_published(self, event_id, worker_id):
        self.published.append((event_id, worker_id))

    def mark_retrying(
        self,
        event_id,
        worker_id,
        *,
        error_code,
        available_at,
    ):
        delay = round(
            (available_at - datetime.now(timezone.utc)).total_seconds()
        )
        self.retried.append(
            RetryCall(event_id, worker_id, error_code, delay)
        )

    def mark_dead_letter(
        self,
        event_id,
        worker_id,
        *,
        error_code,
    ):
        self.dead_lettered.append((event_id, worker_id, error_code))


class CapturingSink:
    def __init__(self):
        self.payloads = []

    def publish(self, payload):
        self.payloads.append(payload)


class FailingSink:
    def __init__(self, error):
        self.error = error

    def publish(self, payload):
        raise self.error


def test_success_is_marked_published():
    repository = FakeRepository([make_claim("event-1")])
    sink = CapturingSink()

    assert RuntimeOutboxDispatcher(
        repository,
        sink,
    ).run_once("worker-1") == 1

    assert repository.published == [("event-1", "worker-1")]
    assert sink.payloads[0]["event_id"] == "event-1"


def test_transient_delivery_uses_bounded_delay():
    repository = FakeRepository(
        [make_claim("event-1", attempt_count=2)]
    )

    RuntimeOutboxDispatcher(
        repository,
        FailingSink(RuntimeError()),
    ).run_once("worker-1")

    assert repository.retried[0].error_code == "unexpected_error"
    assert repository.retried[0].delay_seconds == 5


def test_exhausted_delivery_dead_letters():
    repository = FakeRepository(
        [make_claim("event-1", attempt_count=5, max_attempts=5)]
    )

    RuntimeOutboxDispatcher(
        repository,
        FailingSink(RuntimeError()),
    ).run_once("worker-1")

    assert repository.dead_lettered == [
        ("event-1", "worker-1", "unexpected_error")
    ]
