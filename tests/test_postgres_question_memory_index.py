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
