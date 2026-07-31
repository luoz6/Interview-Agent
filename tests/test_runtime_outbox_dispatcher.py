from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Event

from app.services.workflow_thread_lock import (
    GenerationLeaseLost,
    ProjectionConflict,
)

from app.services.runtime_outbox_dispatcher import (
    CeleryRuntimeEventSink,
    LocalRuntimeEventSink,
    RuntimeOutboxDispatcher,
)


def make_claim(
    event_id: str,
    *,
    attempt_count: int = 1,
    max_attempts: int = 5,
    event_type: str = "round_closed",
) -> dict:
    return {
        "event_id": event_id,
        "payload": {"event_id": event_id, "event_type": event_type},
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
        self.heartbeats = 0
        self.heartbeat_batches = []

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

    def extend_outbox_lease(self, event_id, worker_id, lease_seconds):
        self.heartbeats += 1
        return True

    def extend_outbox_leases(self, event_ids, worker_id, lease_seconds):
        self.heartbeats += 1
        self.heartbeat_batches.append(set(event_ids))
        return True


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


class BlockingSink:
    def __init__(self, release):
        self.release = release

    def publish(self, payload):
        self.release.wait(0.25)


class RecordingSignalStore:
    def __init__(self, *, fail=False):
        self.calls = []
        self.fail = fail

    def increment(self, *, workflow_type, signal_code):
        self.calls.append((workflow_type, signal_code))
        if self.fail:
            raise RuntimeError("signal database unavailable")


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


def test_interview_projection_conflict_records_one_incident_and_dead_letters():
    repository = FakeRepository(
        [make_claim("event-1", event_type="interview_command_ready")]
    )
    signals = RecordingSignalStore()

    RuntimeOutboxDispatcher(
        repository,
        FailingSink(ProjectionConflict()),
        signal_store=signals,
    ).run_once("worker-1")

    assert signals.calls == [("interview", "projection_conflict")]
    assert repository.dead_lettered == [
        ("event-1", "worker-1", "projection_conflict")
    ]


def test_generation_lease_loss_records_one_interview_incident():
    repository = FakeRepository(
        [make_claim("event-1", event_type="interview_command_ready")]
    )
    signals = RecordingSignalStore()
    error = GenerationLeaseLost("renewal ownership unavailable")
    error.__cause__ = RuntimeError(
        "postgresql://private lease_token=private provider payload"
    )

    RuntimeOutboxDispatcher(
        repository,
        FailingSink(error),
        signal_store=signals,
    ).run_once("worker-1")

    assert signals.calls == [("interview", "generation_lease_lost")]
    assert repository.retried[0].error_code == "generation_lease_lost"
    assert "private" not in repr(signals.calls)


def test_signal_write_failure_preserves_original_outbox_transition():
    repository = FakeRepository(
        [make_claim("event-1", event_type="interview_command_ready")]
    )
    signals = RecordingSignalStore(fail=True)

    RuntimeOutboxDispatcher(
        repository,
        FailingSink(ProjectionConflict()),
        signal_store=signals,
    ).run_once("worker-1")

    assert signals.calls == [("interview", "projection_conflict")]
    assert repository.dead_lettered == [
        ("event-1", "worker-1", "projection_conflict")
    ]


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


def test_dispatcher_heartbeats_long_running_sink():
    repository = FakeRepository([make_claim("event-1")])
    release = Event()

    RuntimeOutboxDispatcher(
        repository,
        BlockingSink(release),
        lease_seconds=3,
        heartbeat_seconds=0.05,
    ).run_once("worker-1")

    assert repository.heartbeats >= 1


def test_dispatcher_heartbeats_all_claimed_events_while_first_is_running():
    repository = FakeRepository(
        [make_claim("event-1"), make_claim("event-2")]
    )
    release = Event()

    RuntimeOutboxDispatcher(
        repository,
        BlockingSink(release),
        lease_seconds=3,
        heartbeat_seconds=0.05,
    ).run_once("worker-1")

    assert {"event-1", "event-2"} in repository.heartbeat_batches
    assert repository.published == [
        ("event-1", "worker-1"),
        ("event-2", "worker-1"),
    ]


def test_celery_sink_routes_review_retry_to_review_workflow_task():
    class FakeCelery:
        def __init__(self):
            self.calls = []

        def send_task(self, name, args):
            self.calls.append((name, args))

    app = FakeCelery()
    payload = {"event_type": "review_retry_due", "event_id": "retry-1"}

    CeleryRuntimeEventSink(celery_app=app).publish(payload)

    assert app.calls == [
        (
            "app.services.review_workflow_tasks.run_review_workflow_event",
            [payload],
        )
    ]


def test_local_sink_routes_principal_memory_event_without_round_review():
    class Consumer:
        def __init__(self):
            self.payloads = []

        def consume(self, payload):
            self.payloads.append(payload)

    consumer = Consumer()
    sink = LocalRuntimeEventSink(
        control_store=object(),
        worker_id="worker",
        principal_memory_consumer=consumer,
    )
    payload = {
        "event_type": "principal_memory_proposal_requested_v1",
        "event_id": "event-principal",
    }

    sink.publish(payload)

    assert consumer.payloads == [payload]
