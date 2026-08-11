from __future__ import annotations

import pytest

from app.adapters.postgres import session_repository as repositories
from app.adapters.postgres.session_repository import PostgresSessionRepository
from app.adapters.postgres.unit_of_work import PostgresUnitOfWork
from app.domain.interview.errors import SessionVersionConflict
from app.services.postgres_connections import DirectPsycopg2ConnectionProvider


SESSION_ROW = {
    "session_id": "session-1",
    "plan_json": {},
    "current_index": 0,
    "status": "active",
    "phase": "interview",
    "phase_status": "active",
    "review_status": "not_started",
    "job_description": "role",
    "resume_text": "resume",
    "job_tags": [],
    "decision_json": None,
    "pending_output": None,
    "skipped_question_ids": [],
    "started_at": "2026-01-01T00:00:00Z",
    "finished_at": None,
    "state_version": 2,
    "checkpoint_version": 0,
    "last_checkpoint_at": None,
    "last_command_id": None,
    "workflow_engine": "legacy",
    "graph_schema_version": "legacy",
    "memory_policy_version": "deterministic-v1",
    "projection_sha256": None,
    "deletion_status": "active",
    "row_schema_version": "session-row-v1",
}


class FakeCursor:
    def __init__(self, *, rowcount=1, fetchone=None):
        self.rowcount = rowcount
        self._fetchone = fetchone
        self.statements = []
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.closed = True
        return False

    def execute(self, statement, params=None):
        self.statements.append((statement, params))

    def fetchone(self):
        return self._fetchone


class FakeConnection:
    def __init__(self, cursor):
        self.autocommit = False
        self._cursor = cursor
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def cursor(self):
        return self._cursor

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


class FakeMessages:
    messages_table = "test_messages"

    def __init__(self):
        self.replacements = 0

    def replace_messages(self, cursor, state):
        self.replacements += 1


class FakeOutbox:
    def __init__(self, *, inserted):
        self.inserted = inserted
        self.calls = 0

    def enqueue_event(self, cursor, event):
        self.calls += 1
        return self.inserted


def _runtime(*, rowcount=1, fetchone=None, outbox_inserted=True):
    cursor = FakeCursor(rowcount=rowcount, fetchone=fetchone)
    connection = FakeConnection(cursor)
    provider = DirectPsycopg2ConnectionProvider(
        "redacted",
        connect=lambda dsn: connection,
    )
    messages = FakeMessages()
    outbox = FakeOutbox(inserted=outbox_inserted)
    repository = PostgresSessionRepository(
        provider,
        sessions_table="test_sessions",
        message_repository=messages,
        runtime_outbox_repository=outbox,
    )
    return repository, PostgresUnitOfWork(provider), connection, messages, outbox


@pytest.fixture(autouse=True)
def stable_session_row(monkeypatch):
    monkeypatch.setattr(
        repositories.SessionRowMapper,
        "to_row",
        lambda state: dict(SESSION_ROW),
    )


def test_cas_conflict_rolls_back_message_and_session_work():
    repository, unit, connection, messages, outbox = _runtime(
        rowcount=0,
        fetchone=(7,),
    )

    with pytest.raises(SessionVersionConflict) as captured:
        with unit as active:
            repository.replace_state(
                active.cursor,
                {"session_id": "session-1", "messages": []},
                expected_previous_version=1,
            )
            active.commit()

    assert captured.value.expected_version == 1
    assert captured.value.actual_version == 7
    assert messages.replacements == 1
    assert outbox.calls == 0
    assert connection.commits == 0
    assert connection.rollbacks == 1
    assert unit.state == "rolled_back"


def test_duplicate_outbox_event_rolls_back_session_and_message_work():
    repository, unit, connection, messages, outbox = _runtime(
        outbox_inserted=False
    )

    with pytest.raises(RuntimeError, match="runtime event already exists"):
        with unit as active:
            repository.replace_state(
                active.cursor,
                {"session_id": "session-1", "messages": []},
                expected_previous_version=1,
                outbox_event=object(),
            )
            active.commit()

    assert messages.replacements == 1
    assert outbox.calls == 1
    assert connection.commits == 0
    assert connection.rollbacks == 1
    assert unit.state == "rolled_back"


def test_successful_cas_and_outbox_share_one_commit():
    repository, unit, connection, messages, outbox = _runtime()

    with unit as active:
        repository.replace_state(
            active.cursor,
            {"session_id": "session-1", "messages": []},
            expected_previous_version=1,
            outbox_event=object(),
        )
        active.commit()

    assert messages.replacements == 1
    assert outbox.calls == 1
    assert connection.commits == 1
    assert connection.rollbacks == 0
    assert unit.state == "committed"
