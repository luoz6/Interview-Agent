from __future__ import annotations

from contextlib import contextmanager
import re

from app.services.postgres_question_memory_index import (
    PostgresQuestionMemoryIndexStore,
)


def test_question_memory_table_and_index_names_follow_runtime_prefix(monkeypatch):
    monkeypatch.setattr(
        PostgresQuestionMemoryIndexStore,
        "_ensure_schema",
        lambda self: None,
    )
    provider = type("Provider", (), {})()

    store = PostgresQuestionMemoryIndexStore(
        connection_provider=provider,
        table_prefix="memory_test",
        schema_mode="migrate",
    )

    assert store.table == "memory_test_question_memory_refs"
    assert store._connection_provider is provider


class _SchemaCursor:
    def __init__(self, statements):
        self.statements = statements

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, statement, params=None):
        self.statements.append((str(statement), params))


class _SchemaConnection:
    def __init__(self):
        self.statements = []
        self.commits = 0

    def cursor(self):
        return _SchemaCursor(self.statements)

    def commit(self):
        self.commits += 1


class _SchemaProvider:
    def __init__(self):
        self.connection_object = _SchemaConnection()

    @contextmanager
    def connection(self):
        yield self.connection_object


def test_fresh_question_memory_ddl_owns_nullable_strict_positive_target():
    provider = _SchemaProvider()
    store = object.__new__(PostgresQuestionMemoryIndexStore)
    store.table_prefix = "memory_test"
    store.table = "memory_test_question_memory_refs"
    store._connection_provider = provider

    store._ensure_schema()

    create = next(
        statement
        for statement, _params in provider.connection_object.statements
        if "CREATE TABLE IF NOT EXISTS" in statement
    )
    target_tail = re.search(
        r"resolved_target_output_tokens\s+INTEGER(?P<tail>.*)",
        create,
        flags=re.IGNORECASE | re.DOTALL,
    )
    assert target_tail is not None
    assert not re.match(r"\s+NOT\s+NULL\b", target_tail.group("tail"), re.I)
    assert "memory_test_question_memory_resolved_target_check" in create
    assert re.search(
        r"CHECK\s*\(\s*resolved_target_output_tokens\s*>\s*0\s*\)",
        create,
        flags=re.IGNORECASE,
    )
    assert provider.connection_object.commits == 1
