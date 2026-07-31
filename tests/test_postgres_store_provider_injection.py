from contextlib import contextmanager

import pytest

from app.services.interview_generation_store import PostgresInterviewGenerationStore
from app.services.context_artifact_store import PostgresContextArtifactStore
from app.services.postgres_question_memory_index import (
    PostgresQuestionMemoryIndexStore,
)
from app.services.interview_workflow_store import PostgresInterviewWorkflowStore
from app.services.postgres_identifiers import PostgresIdentifierInvalid
from app.services.postgres_runtime_control import PostgresRuntimeControlStore
from app.services.postgres_session import PostgresInterviewSessionStore
from app.services.report_jobs import PostgresReportJobStore
from app.services.review_workflow_store import PostgresReviewWorkflowStore
from app.services.runtime_signal_metrics import PostgresRuntimeSignalStore


class BorrowedProvider:
    def __init__(self):
        self.connection_calls = 0
        self.close_calls = 0

    @contextmanager
    def connection(self):
        self.connection_calls += 1
        yield object()

    def close(self, timeout=None):
        self.close_calls += 1


@pytest.fixture
def no_schema_setup(monkeypatch):
    classes = (
        PostgresInterviewGenerationStore,
        PostgresInterviewWorkflowStore,
        PostgresRuntimeControlStore,
        PostgresInterviewSessionStore,
        PostgresReportJobStore,
        PostgresReviewWorkflowStore,
        PostgresRuntimeSignalStore,
        PostgresContextArtifactStore,
        PostgresQuestionMemoryIndexStore,
    )
    for cls in classes:
        monkeypatch.setattr(cls, "_ensure_schema", lambda self: None)


@pytest.mark.parametrize(
    "store_type",
    [
        PostgresRuntimeControlStore,
        PostgresInterviewGenerationStore,
        PostgresReportJobStore,
        PostgresRuntimeSignalStore,
        PostgresContextArtifactStore,
        PostgresQuestionMemoryIndexStore,
    ],
)
def test_store_uses_injected_provider_as_only_connection_source(
    monkeypatch, no_schema_setup, store_type
):
    import psycopg2

    monkeypatch.setattr(
        psycopg2,
        "connect",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("DSN fallback must not be used")
        ),
    )
    provider = BorrowedProvider()

    store = store_type(
        dsn="postgresql://intentionally-invalid",
        connection_provider=provider,
        table_prefix="test_provider",
        schema_mode="migrate",
    )

    assert store._connection_provider is provider
    assert store._provider_is_owned is False
    assert provider.connection_calls == 0
    assert provider.close_calls == 0


@pytest.mark.parametrize(
    "store_type",
    [PostgresInterviewWorkflowStore, PostgresReviewWorkflowStore],
)
def test_nested_workflow_store_propagates_exact_provider_identity(
    no_schema_setup, store_type
):
    provider = BorrowedProvider()

    store = store_type(
        dsn="compatibility-metadata-only",
        connection_provider=provider,
        table_prefix="test_nested",
        schema_mode="migrate",
    )

    assert store._connection_provider is provider
    assert store.control._connection_provider is provider


def test_session_store_propagates_exact_provider_identity(no_schema_setup):
    provider = BorrowedProvider()

    store = PostgresInterviewSessionStore(
        dsn="compatibility-metadata-only",
        connection_provider=provider,
        table_prefix="test_session_nested",
        schema_mode="migrate",
    )

    assert store._connection_provider is provider
    assert store._runtime_control._connection_provider is provider


def test_invalid_prefix_fails_before_provider_is_used(no_schema_setup):
    provider = BorrowedProvider()

    with pytest.raises(PostgresIdentifierInvalid):
        PostgresRuntimeControlStore(
            connection_provider=provider,
            table_prefix="unsafe-prefix",
            schema_mode="migrate",
        )

    assert provider.connection_calls == 0


def test_injected_provider_requires_explicit_schema_mode(no_schema_setup):
    with pytest.raises(ValueError, match="explicit schema_mode"):
        PostgresRuntimeControlStore(
            connection_provider=BorrowedProvider(),
            table_prefix="test_explicit_mode",
        )
