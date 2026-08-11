from contextlib import contextmanager

from app.adapters.postgres.store_schema_adapter import (
    PostgresRuntimeControlSchemaAdapter,
    PostgresSessionSchemaAdapter,
)


class RecordingCursor:
    def __init__(self):
        self.statements = []
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.closed = True
        return False

    def execute(self, statement, params=None):
        self.statements.append((statement, params))


class RecordingConnection:
    def __init__(self):
        self.cursor_instance = RecordingCursor()

    def cursor(self):
        return self.cursor_instance


class RecordingProvider:
    def __init__(self):
        self.connection_calls = 0
        self.connection_instance = RecordingConnection()

    @contextmanager
    def connection(self):
        self.connection_calls += 1
        yield self.connection_instance


def _rendered_statements(provider: RecordingProvider) -> str:
    return "\n".join(
        repr(statement)
        for statement, _ in provider.connection_instance.cursor_instance.statements
    )


def test_session_schema_adapter_owns_session_schema_sql():
    provider = RecordingProvider()
    adapter = PostgresSessionSchemaAdapter(
        provider,
        table_prefix="test_schema",
        sessions_table="test_schema_sessions",
        messages_table="test_schema_messages",
        reports_table="test_schema_reports",
        question_evaluations_table="test_schema_question_evaluations",
    )

    adapter.ensure_schema()

    cursor = provider.connection_instance.cursor_instance
    rendered = _rendered_statements(provider)
    assert provider.connection_calls == 1
    assert cursor.closed is True
    assert len(cursor.statements) >= 20
    assert "test_schema_sessions" in rendered
    assert "test_schema_messages" in rendered
    assert "test_schema_reports" in rendered
    assert "test_schema_question_evaluations" in rendered
    assert "test_schema_messages_session_idx" in rendered


def test_runtime_control_schema_adapter_owns_runtime_schema_and_indexes():
    provider = RecordingProvider()
    adapter = PostgresRuntimeControlSchemaAdapter(
        provider,
        table_prefix="test_schema",
        sessions_table="test_schema_sessions",
        outbox_table="test_schema_runtime_outbox",
        receipts_table="test_schema_runtime_event_receipts",
        agent_runs_table="test_schema_agent_runs",
    )

    adapter.ensure_schema()

    cursor = provider.connection_instance.cursor_instance
    rendered = _rendered_statements(provider)
    assert provider.connection_calls == 1
    assert cursor.closed is True
    assert len(cursor.statements) >= 10
    assert "test_schema_sessions" in rendered
    assert "test_schema_runtime_outbox" in rendered
    assert "test_schema_runtime_event_receipts" in rendered
    assert "test_schema_agent_runs" in rendered
    assert "test_schema_runtime_outbox_running_lease_idx" in rendered
