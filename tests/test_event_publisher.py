import pytest

from app.services.event_publisher import (
    CeleryRuntimeEventPublisher,
    LocalRoundReviewEventPublisher,
    NoopRuntimeEventPublisher,
)
from app.services.runtime_domain_events import RoundClosedEvent


class FakeCeleryApp:
    def __init__(self):
        self.calls = []

    def send_task(self, name: str, args=None, kwargs=None):
        self.calls.append((name, args or [], kwargs or {}))


def test_celery_worker_imports_round_review_task():
    from app.services.celery_app import celery_app

    assert "app.services.round_review_tasks" in celery_app.conf.include


class FakeExecutor:
    def __init__(self):
        self.calls = []

    def submit(self, fn, payload):
        self.calls.append((fn, payload))
        return object()

    def shutdown(self, *, wait=True):
        self.shutdown_wait = wait


def test_noop_runtime_event_publisher_still_ignores_events():
    publisher = NoopRuntimeEventPublisher()

    assert publisher.publish({"event": "ignored"}) is None


def test_local_round_review_event_publisher_schedules_round_closed_event():
    executor = FakeExecutor()
    publisher = LocalRoundReviewEventPublisher(executor=executor)

    publisher.publish(
        RoundClosedEvent(
            session_id="s1",
            correlation_id="prep-123",
            causation_id="cmd-2",
            state_version=3,
            question_id="q1",
            answer_state="answered",
            job_tags=["python", "redis"],
        )
    )

    assert len(executor.calls) == 1
    fn, payload = executor.calls[0]
    assert fn.__name__ == "run_round_review_event_payload"
    assert payload["event_type"] == "round_closed"
    assert payload["session_id"] == "s1"
    assert payload["question_id"] == "q1"
    assert payload["answer_state"] == "answered"
    assert payload["schema_version"] == "runtime-event-v1"
    assert payload["event_id"].startswith("event-")
    assert payload["correlation_id"] == "prep-123"
    assert payload["causation_id"] == "cmd-2"
    assert payload["state_version"] == 3


def test_round_closed_event_accepts_legacy_payload_defaults():
    event = RoundClosedEvent.model_validate(
        {
            "event_type": "round_closed",
            "session_id": "s1",
            "question_id": "q1",
            "answer_state": "answered",
        }
    )

    assert event.schema_version == "runtime-event-v1"
    assert event.event_id.startswith("event-")
    assert event.correlation_id == "s1"
    assert event.state_version is None


def test_local_round_review_event_publisher_rejects_unknown_event_type():
    publisher = LocalRoundReviewEventPublisher(executor=FakeExecutor())

    with pytest.raises(ValueError, match="unsupported runtime event"):
        publisher.publish({"event_type": "unknown"})


def test_local_round_review_event_publisher_shutdown_drains_executor():
    executor = FakeExecutor()
    publisher = LocalRoundReviewEventPublisher(executor=executor)

    publisher.shutdown(wait=False)

    assert executor.shutdown_wait is False


def test_celery_runtime_event_publisher_routes_round_closed_event():
    app = FakeCeleryApp()
    publisher = CeleryRuntimeEventPublisher(celery_app=app)

    publisher.publish(
        RoundClosedEvent(
            session_id="s1",
            correlation_id="prep-123",
            causation_id="cmd-2",
            state_version=3,
            question_id="q1",
            answer_state="answered",
            job_tags=["python", "redis"],
        )
    )

    assert len(app.calls) == 1
    name, args, kwargs = app.calls[0]
    assert name == "app.services.round_review_tasks.run_closed_round_review"
    assert kwargs == {}
    payload = args[0]
    assert payload["event_type"] == "round_closed"
    assert payload["session_id"] == "s1"
    assert payload["question_id"] == "q1"
    assert payload["answer_state"] == "answered"
    assert payload["job_tags"] == ["python", "redis"]
    assert payload["schema_version"] == "runtime-event-v1"
    assert payload["event_id"].startswith("event-")
    assert payload["correlation_id"] == "prep-123"
    assert payload["causation_id"] == "cmd-2"
    assert payload["state_version"] == 3
    assert isinstance(payload["emitted_at"], str)
    assert payload["emitted_at"].endswith("Z")


def test_celery_runtime_event_publisher_rejects_unknown_event_type():
    app = FakeCeleryApp()
    publisher = CeleryRuntimeEventPublisher(celery_app=app)

    with pytest.raises(ValueError, match="unsupported runtime event"):
        publisher.publish({"event_type": "unknown"})
