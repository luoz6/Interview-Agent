import pytest

from app.services.postgres_connections import DirectPsycopg2ConnectionProvider
from app.services.postgres_runtime_control import PostgresRuntimeControlStore


class FakeCursor:
    def __init__(self):
        self.closed = False
        self.mutations: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.closed = True
        return False


class FakeConnection:
    def __init__(self):
        self.autocommit = False
        self.closed = False
        self.commits = 0
        self.rollbacks = 0
        self.last_cursor: FakeCursor | None = None

    def cursor(self):
        self.last_cursor = FakeCursor()
        return self.last_cursor

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


class FakeOutboxRepository:
    def __init__(self):
        self.failures: dict[str, BaseException] = {}

    def _mutate(self, cursor, name, result):
        cursor.mutations.append(name)
        failure = self.failures.get(name)
        if failure is not None:
            raise failure
        return result

    def claim_batch(self, cursor, **kwargs):
        return self._mutate(cursor, "claim_batch", [{"event_id": "event-1"}])

    def replay_dead_letter(self, cursor, event_id):
        cursor.mutations.extend(["replay_outbox", "reset_receipt"])
        failure = self.failures.get("replay_dead_letter")
        if failure is not None:
            raise failure
        return {"event_id": event_id, "status": "pending"}


class FakeReceiptRepository:
    def __init__(self):
        self.failures: dict[str, BaseException] = {}

    def complete_round_review(
        self,
        cursor,
        event_id,
        consumer_name,
        worker_id,
        record,
    ):
        cursor.mutations.extend(["upsert_evaluation", "complete_receipt"])
        failure = self.failures.get("complete_round_review")
        if failure is not None:
            raise failure
        return {"event_id": event_id, "status": "completed"}

    def fail_round_review(
        self,
        cursor,
        event_id,
        consumer_name,
        worker_id,
        record,
        *,
        error_code,
    ):
        cursor.mutations.extend(["upsert_evaluation", "fail_receipt"])
        failure = self.failures.get("fail_round_review")
        if failure is not None:
            raise failure
        return {"event_id": event_id, "status": "dead_letter"}


def _facade():
    connections: list[FakeConnection] = []

    def connect(_dsn):
        connection = FakeConnection()
        connections.append(connection)
        return connection

    facade = PostgresRuntimeControlStore.__new__(PostgresRuntimeControlStore)
    facade._connection_provider = DirectPsycopg2ConnectionProvider(
        "redacted",
        connect=connect,
    )
    facade._outbox_repository = FakeOutboxRepository()
    facade._receipt_repository = FakeReceiptRepository()
    return facade, connections


def _assert_rolled_back(connection: FakeConnection, mutations: list[str]):
    assert connection.last_cursor.mutations == mutations
    assert connection.commits == 0
    assert connection.rollbacks == 1
    assert connection.last_cursor.closed is True
    assert connection.closed is True


def test_claim_batch_failure_rolls_back_without_commit():
    facade, connections = _facade()
    facade._outbox_repository.failures["claim_batch"] = RuntimeError(
        "claim update failed"
    )

    with pytest.raises(RuntimeError, match="claim update failed"):
        facade.claim_batch(worker_id="worker-1", limit=10, lease_seconds=30)

    _assert_rolled_back(connections[0], ["claim_batch"])


def test_dead_letter_replay_receipt_reset_failure_rolls_back_both_writes():
    facade, connections = _facade()
    facade._outbox_repository.failures["replay_dead_letter"] = RuntimeError(
        "receipt reset failed"
    )

    with pytest.raises(RuntimeError, match="receipt reset failed"):
        facade.replay_dead_letter("event-1")

    _assert_rolled_back(
        connections[0],
        ["replay_outbox", "reset_receipt"],
    )


def test_receipt_lease_loss_rolls_back_evaluation_and_receipt():
    facade, connections = _facade()
    facade._receipt_repository.failures["complete_round_review"] = RuntimeError(
        "runtime receipt lease was lost"
    )

    with pytest.raises(RuntimeError, match="lease was lost"):
        facade.complete_round_review(
            "event-1",
            "review-consumer",
            "worker-1",
            object(),
        )

    _assert_rolled_back(
        connections[0],
        ["upsert_evaluation", "complete_receipt"],
    )


def test_evaluation_conflict_leaves_failed_receipt_unchanged():
    facade, connections = _facade()
    facade._receipt_repository.failures["fail_round_review"] = ValueError(
        "review_engine conflicts with persisted evaluation"
    )

    with pytest.raises(ValueError, match="review_engine conflicts"):
        facade.fail_round_review(
            "event-1",
            "review-consumer",
            "worker-1",
            object(),
            error_code="review_failed",
        )

    _assert_rolled_back(
        connections[0],
        ["upsert_evaluation", "fail_receipt"],
    )


def test_successful_runtime_mutation_commits_once():
    facade, connections = _facade()

    result = facade.complete_round_review(
        "event-1",
        "review-consumer",
        "worker-1",
        object(),
    )

    connection = connections[0]
    assert result == {"event_id": "event-1", "status": "completed"}
    assert connection.last_cursor.mutations == [
        "upsert_evaluation",
        "complete_receipt",
    ]
    assert connection.commits == 1
    assert connection.rollbacks == 0
    assert connection.last_cursor.closed is True
    assert connection.closed is True
