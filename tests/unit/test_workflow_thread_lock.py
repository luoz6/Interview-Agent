from threading import Event
from types import SimpleNamespace
from contextlib import contextmanager

import pytest

import app.services.review_workflow_store as review_store_module
from app.services.review_workflow_store import (
    PostgresReviewWorkflowStore,
    ReviewEffectHeartbeat,
)
from app.domain.interview.errors import SessionVersionConflict
from app.services.workflow_thread_lock import (
    FencedWriteRejected,
    NoopWorkflowThreadLock,
    PostgresWorkflowThreadLock,
    ReviewEffectLeaseLost,
    WorkflowThreadBusy,
    advisory_lock_key,
    interview_thread_identity,
    review_thread_identity,
)


class FakeCursor:
    def __init__(self, connection):
        self.connection = connection
        self.row = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def execute(self, statement, params=None):
        self.connection.statements.append((statement, params))
        if "pg_try_advisory_lock" in statement:
            self.row = (self.connection.acquire_results.pop(0),)
        elif "pg_advisory_unlock" in statement:
            self.row = (True,)
        else:
            self.row = (1,)

    def fetchone(self):
        return self.row


class FakeConnection:
    def __init__(self, acquire_results):
        self.acquire_results = list(acquire_results)
        self.statements = []
        self.autocommit = False
        self.closed = False

    def cursor(self):
        return FakeCursor(self)

    def close(self):
        self.closed = True


def test_lock_key_uses_fixed_stable_vectors():
    assert interview_thread_identity("session-1") == "interview:session-1"
    assert review_thread_identity("job-1") == "review:job-1"
    assert advisory_lock_key("interview:session-1") == -6673549550733862059
    assert advisory_lock_key("review:job-1") == 5856102020232027748


def test_interview_and_review_namespaces_do_not_collide():
    assert advisory_lock_key(interview_thread_identity("same")) != advisory_lock_key(
        review_thread_identity("same")
    )


@pytest.mark.parametrize(
    "lock",
    [
        NoopWorkflowThreadLock(),
        PostgresWorkflowThreadLock(
            dsn="unused",
            connect=lambda _dsn: FakeConnection([True]),
        ),
    ],
)
def test_lock_context_preserves_frozen_domain_exception(lock):
    with pytest.raises(SessionVersionConflict) as raised:
        with lock.hold(
            interview_thread_identity("session-frozen"),
            workflow_type="interview",
        ):
            raise SessionVersionConflict(expected_version=1, actual_version=2)

    assert raised.value.expected_version == 1
    assert raised.value.actual_version == 2


@pytest.mark.parametrize("value", ["", " ", None])
def test_identity_rejects_empty_identifiers(value):
    with pytest.raises(ValueError):
        interview_thread_identity(value)


def test_lock_contention_uses_bounded_backoff_not_busy_spin():
    connection = FakeConnection([False, False, False])
    clock = iter([0.0, 0.0, 0.01, 0.02])
    sleeps = []
    lock = PostgresWorkflowThreadLock(
        dsn="unused",
        default_timeout_seconds=0.02,
        initial_backoff_seconds=0.01,
        max_backoff_seconds=0.02,
        jitter_ratio=0,
        connect=lambda _dsn: connection,
        monotonic_fn=lambda: next(clock),
        sleep_fn=sleeps.append,
    )

    with pytest.raises(WorkflowThreadBusy):
        with lock.hold("interview:s1", workflow_type="interview"):
            pass

    assert sleeps == [0.01, 0.01]
    assert connection.autocommit is True
    assert connection.closed is True


def test_lock_rejects_provider_and_legacy_connect_together():
    with pytest.raises(ValueError, match="mutually exclusive"):
        PostgresWorkflowThreadLock(
            dsn="unused",
            exclusive_provider=SimpleNamespace(),
            connect=lambda _dsn: FakeConnection([True]),
        )


def test_lock_borrows_exclusive_provider_without_closing_healthy_session():
    connection = FakeConnection([True])

    class Provider:
        def __init__(self):
            self.borrowed = 0

        @contextmanager
        def exclusive_connection(self, *, autocommit):
            assert autocommit is True
            self.borrowed += 1
            connection.autocommit = autocommit
            yield connection

    provider = Provider()
    lock = PostgresWorkflowThreadLock(exclusive_provider=provider)

    with lock.hold("interview:s1", workflow_type="interview"):
        assert connection.closed is False

    assert provider.borrowed == 1
    assert connection.closed is False


def test_lock_releases_and_closes_after_exception():
    connection = FakeConnection([True])
    lock = PostgresWorkflowThreadLock(
        dsn="unused", connect=lambda _dsn: connection
    )

    with pytest.raises(RuntimeError, match="boom"):
        with lock.hold("review:j1", workflow_type="review"):
            raise RuntimeError("boom")

    assert any("pg_advisory_unlock" in sql for sql, _ in connection.statements)
    assert connection.closed is True


def test_noop_lock_validates_identity():
    with NoopWorkflowThreadLock().hold(
        "interview:s1", workflow_type="interview"
    ) as ownership:
        assert ownership is None


def test_review_effect_lease_lost_is_fenced_write_compatible():
    error = ReviewEffectLeaseLost("claim lost")

    assert isinstance(error, FencedWriteRejected)


def test_review_effect_heartbeat_exception_fails_closed_with_original_cause():
    failure = RuntimeError("renewal unavailable")

    class RaisingStore:
        def __init__(self):
            self.called = Event()

        def assert_effect_owned(self, claim):
            return True

        def heartbeat_effect(self, *args, **kwargs):
            self.called.set()
            raise failure

    store = RaisingStore()
    heartbeat = ReviewEffectHeartbeat(
        store,
        SimpleNamespace(),
        lease_seconds=30,
    )
    heartbeat.interval_seconds = 0.01

    with heartbeat:
        assert store.called.wait(timeout=1)
        assert heartbeat._thread is not None
        heartbeat._thread.join(timeout=1)
        with pytest.raises(ReviewEffectLeaseLost) as caught:
            heartbeat.ensure_owned()

    assert caught.value.__cause__ is failure
    assert heartbeat._thread is not None
    assert not heartbeat._thread.is_alive()


@pytest.mark.parametrize(
    "provider",
    [
        lambda: (_ for _ in ()).throw(RuntimeError("provider failed")),
        lambda: "not-a-json-object",
    ],
)
def test_review_effect_loss_takes_precedence_before_failure_mutation(
    monkeypatch, provider
):
    claim = SimpleNamespace(
        status="running",
        claim_token="claim-token",
    )
    failure = ReviewEffectLeaseLost("claim lost")
    failed_claims = []

    class LostHeartbeat:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def ensure_owned(self):
            raise failure

    store = SimpleNamespace(
        effect_lease_seconds=30,
        claim_effect=lambda **kwargs: claim,
        fail_effect=failed_claims.append,
        complete_effect=lambda *args: pytest.fail(
            "lost effect must not complete"
        ),
    )
    monkeypatch.setattr(
        review_store_module, "ReviewEffectHeartbeat", LostHeartbeat
    )

    with pytest.raises(ReviewEffectLeaseLost) as caught:
        PostgresReviewWorkflowStore.run_effect(
            store,
            operation_key="effect-1",
            job_id="job-1",
            effect_type="question_review",
            graph_schema_version="langgraph-review-v1",
            input_sha256="input-1",
            provider=provider,
        )

    assert caught.value is failure
    assert failed_claims == []
