from __future__ import annotations

from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.adapters.persistence.postgres.runtime_control import (
    PostgresRuntimeControlStore,
)
from app.adapters.postgres.connections import DirectPsycopg2ConnectionProvider
from app.domain.execution_lease import LeaseToken
from app.domain.interview.scheduling import InvocationIdentity


class InjectedCommitFailure(RuntimeError):
    pass


class RecordingCursor:
    def __init__(self) -> None:
        self.operations: list[str] = []
        self.rowcount = 1

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, _statement, _params=None) -> None:
        self.operations.append("command")
        self.rowcount = 1

    def fetchone(self):
        return None


class RecordingConnection:
    def __init__(self) -> None:
        self.autocommit = False
        self.closed = False
        self.commits = 0
        self.rollbacks = 0
        self.recording_cursor = RecordingCursor()

    def cursor(self):
        return nullcontext(self.recording_cursor)

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        self.closed = True


class CommitPhase:
    def __init__(self, phase: str, *, fail_at: str | None) -> None:
        self.phase = phase
        self.fail_at = fail_at

    def _apply(self, cursor) -> None:
        cursor.operations.append(self.phase)
        if self.phase == self.fail_at:
            raise InjectedCommitFailure(self.phase)

    def put_if_absent_with_cursor(self, cursor, *_args) -> None:
        self._apply(cursor)

    def save_with_cursor(self, cursor, *_args) -> None:
        self._apply(cursor)

    def commit_with_cursor(self, cursor, *_args, **_kwargs) -> None:
        self._apply(cursor)

    def enqueue_event(self, cursor, _event) -> None:
        self._apply(cursor)


def _runtime(*, fail_at: str | None = None):
    connections: list[RecordingConnection] = []

    def connect(_dsn):
        connection = RecordingConnection()
        connections.append(connection)
        return connection

    store = object.__new__(PostgresRuntimeControlStore)
    store._connection_provider = DirectPsycopg2ConnectionProvider(
        "redacted",
        connect=connect,
    )
    store.table_prefix = "interview"
    store._execution_artifact_store = CommitPhase(
        "artifact",
        fail_at=fail_at,
    )
    store._scheduler_execution_repository = CommitPhase(
        "state",
        fail_at=fail_at,
    )
    store._agent_invocation_ledger = CommitPhase(
        "ledger",
        fail_at=fail_at,
    )
    store._outbox_repository = CommitPhase("outbox", fail_at=fail_at)
    return store, connections


def _answer_inputs():
    command = SimpleNamespace(
        execution_id="execution-cf04",
        command_id="command-1",
        expected_revision=1,
        payload={"answer_text": "Use a durable queue."},
    )
    artifact = SimpleNamespace(artifact_ref="execution-cf04/answer/command-1")
    state = SimpleNamespace(execution_id="execution-cf04", revision=2)
    return command, artifact, state


def _agent_inputs():
    identity = InvocationIdentity(
        execution_id="execution-cf04",
        task_id="evaluate-answer",
        logical_attempt=1,
    )
    lease = LeaseToken(
        resource_id="execution-cf04:evaluate-answer:1",
        owner_id="scheduler-1",
        token="lease-token",
        fencing_version=1,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=1),
    )
    artifact = SimpleNamespace(artifact_ref="execution-cf04/evaluation/1")
    state = SimpleNamespace(execution_id="execution-cf04", revision=3)
    return identity, lease, artifact, state


@pytest.mark.parametrize(
    ("fail_at", "expected_operations"),
    [
        ("artifact", ["command", "artifact"]),
        ("state", ["command", "artifact", "state"]),
        ("outbox", ["command", "artifact", "state", "outbox"]),
    ],
)
def test_answer_commit_failure_rolls_back_every_staged_write(
    fail_at,
    expected_operations,
):
    store, connections = _runtime(fail_at=fail_at)
    command, artifact, state = _answer_inputs()

    with pytest.raises(InjectedCommitFailure, match=fail_at):
        store.commit_answer(command=command, artifact=artifact, state=state)

    connection = connections[0]
    assert connection.recording_cursor.operations == expected_operations
    assert connection.commits == 0
    assert connection.rollbacks == 1


@pytest.mark.parametrize(
    ("fail_at", "expected_operations"),
    [
        ("artifact", ["artifact"]),
        ("ledger", ["artifact", "ledger"]),
        ("state", ["artifact", "ledger", "state"]),
        ("outbox", ["artifact", "ledger", "state", "outbox"]),
    ],
)
def test_agent_result_commit_failure_rolls_back_every_staged_write(
    fail_at,
    expected_operations,
):
    store, connections = _runtime(fail_at=fail_at)
    identity, lease, artifact, state = _agent_inputs()

    with pytest.raises(InjectedCommitFailure, match=fail_at):
        store.commit_agent_result(
            artifact_ref=artifact.artifact_ref,
            artifact=artifact,
            identity=identity,
            lease=lease,
            state=state,
        )

    connection = connections[0]
    assert connection.recording_cursor.operations == expected_operations
    assert connection.commits == 0
    assert connection.rollbacks == 1


def test_answer_commit_success_uses_one_connection_and_one_commit():
    store, connections = _runtime()
    command, artifact, state = _answer_inputs()

    store.commit_answer(command=command, artifact=artifact, state=state)

    connection = connections[0]
    assert connection.recording_cursor.operations == [
        "command",
        "artifact",
        "state",
        "outbox",
    ]
    assert connection.commits == 1
    assert connection.rollbacks == 0


def test_agent_result_commit_success_uses_one_connection_and_one_commit():
    store, connections = _runtime()
    identity, lease, artifact, state = _agent_inputs()

    store.commit_agent_result(
        artifact_ref=artifact.artifact_ref,
        artifact=artifact,
        identity=identity,
        lease=lease,
        state=state,
    )

    connection = connections[0]
    assert connection.recording_cursor.operations == [
        "artifact",
        "ledger",
        "state",
        "outbox",
    ]
    assert connection.commits == 1
    assert connection.rollbacks == 0
