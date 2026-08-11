import pytest

from app.adapters.postgres.unit_of_work import PostgresUnitOfWork
from app.services.postgres_connections import DirectPsycopg2ConnectionProvider


class FakeCursor:
    def __init__(self, *, close_error=None):
        self.closed = False
        self.close_error = close_error

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.closed = True
        if self.close_error is not None:
            raise self.close_error
        return False


class FakeConnection:
    def __init__(
        self,
        *,
        cursor_close_error=None,
        connection_close_error=None,
        commit_error=None,
    ):
        self.autocommit = False
        self.closed = False
        self.commits = 0
        self.rollbacks = 0
        self.last_cursor = None
        self.cursor_close_error = cursor_close_error
        self.connection_close_error = connection_close_error
        self.commit_error = commit_error

    def cursor(self):
        self.last_cursor = FakeCursor(close_error=self.cursor_close_error)
        return self.last_cursor

    def commit(self):
        self.commits += 1
        if self.commit_error is not None:
            raise self.commit_error

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True
        if self.connection_close_error is not None:
            raise self.connection_close_error


def _runtime(
    *,
    cursor_close_error=None,
    connection_close_error=None,
    commit_error=None,
):
    connections = []

    def connect(_dsn):
        connection = FakeConnection(
            cursor_close_error=cursor_close_error,
            connection_close_error=connection_close_error,
            commit_error=commit_error,
        )
        connections.append(connection)
        return connection

    provider = DirectPsycopg2ConnectionProvider("redacted", connect=connect)
    return PostgresUnitOfWork(provider), connections


def test_explicit_commit_commits_exactly_once_and_closes_resources():
    unit, connections = _runtime()

    with unit as active:
        cursor = active.cursor
        active.commit()

    connection = connections[0]
    assert unit.state == "committed"
    assert connection.commits == 1
    assert connection.rollbacks == 0
    assert connection.closed is True
    assert cursor.closed is True


def test_normal_exit_without_commit_rolls_back():
    unit, connections = _runtime()

    with unit:
        pass

    connection = connections[0]
    assert unit.state == "rolled_back"
    assert connection.commits == 0
    assert connection.rollbacks == 1


def test_explicit_rollback_cannot_be_reversed_to_commit():
    unit, connections = _runtime()

    with unit as active:
        active.rollback()
        with pytest.raises(RuntimeError, match="cannot commit"):
            active.commit()

    assert connections[0].rollbacks == 1


def test_business_exception_rolls_back_and_remains_authoritative():
    unit, connections = _runtime()

    with pytest.raises(ValueError, match="injected failure"):
        with unit:
            raise ValueError("injected failure")

    assert unit.state == "rolled_back"
    assert connections[0].commits == 0
    assert connections[0].rollbacks == 1


def test_cursor_and_decisions_require_active_context():
    unit, _ = _runtime()

    with pytest.raises(RuntimeError, match="cursor is not active"):
        _ = unit.cursor
    with pytest.raises(RuntimeError, match="not active"):
        unit.commit()

    with unit as active:
        active.commit()

    with pytest.raises(RuntimeError, match="cannot be reused"):
        unit.__enter__()


def test_cursor_close_failure_rolls_back_and_closes_connection():
    unit, connections = _runtime(
        cursor_close_error=RuntimeError("cursor close failed")
    )

    with pytest.raises(RuntimeError, match="cursor close failed"):
        with unit as active:
            active.commit()

    connection = connections[0]
    assert unit.state == "rolled_back"
    assert connection.commits == 0
    assert connection.rollbacks == 1
    assert connection.closed is True


def test_business_exception_remains_authoritative_when_cursor_close_fails():
    unit, connections = _runtime(
        cursor_close_error=RuntimeError("cursor close failed")
    )

    with pytest.raises(ValueError, match="business failed"):
        with unit:
            raise ValueError("business failed")

    connection = connections[0]
    assert unit.state == "rolled_back"
    assert connection.commits == 0
    assert connection.rollbacks == 1
    assert connection.closed is True


def test_connection_close_failure_does_not_report_committed_state():
    unit, connections = _runtime(
        connection_close_error=RuntimeError("connection close failed")
    )

    with pytest.raises(RuntimeError, match="connection close failed"):
        with unit as active:
            active.commit()

    connection = connections[0]
    assert unit.state == "rolled_back"
    assert connection.commits == 1
    assert connection.rollbacks == 0
    assert connection.closed is True


def test_business_exception_remains_authoritative_when_connection_close_fails():
    unit, connections = _runtime(
        connection_close_error=RuntimeError("connection close failed")
    )

    with pytest.raises(ValueError, match="business failed"):
        with unit:
            raise ValueError("business failed")

    connection = connections[0]
    assert unit.state == "rolled_back"
    assert connection.commits == 0
    assert connection.rollbacks == 1
    assert connection.closed is True


def test_commit_failure_rolls_back_and_remains_authoritative():
    unit, connections = _runtime(commit_error=RuntimeError("commit failed"))

    with pytest.raises(RuntimeError, match="commit failed"):
        with unit as active:
            active.commit()

    connection = connections[0]
    assert unit.state == "rolled_back"
    assert connection.commits == 1
    assert connection.rollbacks == 1
    assert connection.closed is True
