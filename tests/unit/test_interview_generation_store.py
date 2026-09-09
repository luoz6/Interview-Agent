import inspect
from contextlib import contextmanager

import pytest

from app.services.interview_generation_store import (
    ChunkCoalescer,
    GenerationLeaseConflict,
    PostgresInterviewGenerationStore,
)


def test_chunk_coalescer_uses_injected_clock():
    now = [0.0]
    coalescer = ChunkCoalescer(
        max_interval_seconds=0.2,
        clock=lambda: now[0],
    )

    assert coalescer.add("a") is None
    now[0] = 0.3
    assert coalescer.add("b") == "ab"
    assert coalescer.flush() is None


def test_generation_row_restores_main_question_diagnostics():
    row = (
        "generation-1",
        "session-1",
        "command-1",
        "question-1",
        "completed",
        2,
        "最终问题",
        None,
        None,
        None,
        "main-question-generation-v1",
        "prompt-sha",
        "main_question",
        "identity-sha",
        "intent-sha",
        "context-sha",
        "knowledge-sha",
        "main-question-generator-v1",
        "fallback",
        "provider_timeout",
        2,
        1350,
        True,
        "provider_timeout",
    )

    generation = PostgresInterviewGenerationStore._generation_from_row(row)

    assert generation.provider_invocation_count == 2
    assert generation.generation_latency_ms == 1350
    assert generation.fallback_used is True
    assert generation.safe_reason_code == "provider_timeout"


@pytest.mark.parametrize(
    "diagnostics",
    [
        {"provider_invocation_count": 1},
        {"generation_latency_ms": 10},
        {"fallback_used": False},
        {"safe_reason_code": "generated"},
    ],
)
def test_complete_attempt_rejects_partial_diagnostics(diagnostics):
    store = object.__new__(PostgresInterviewGenerationStore)

    with pytest.raises(ValueError, match="diagnostics must be complete"):
        store.complete_attempt(
            "generation-1",
            1,
            "最终问题",
            lease_token="lease-token",
            fencing_version=1,
            result_mode="generated",
            **diagnostics,
        )


def test_complete_attempt_rejects_diagnostics_without_result_mode():
    store = object.__new__(PostgresInterviewGenerationStore)

    with pytest.raises(ValueError, match="explicit result mode"):
        store.complete_attempt(
            "generation-1",
            1,
            "最终问题",
            lease_token="lease-token",
            fencing_version=1,
            provider_invocation_count=1,
            generation_latency_ms=10,
            fallback_used=False,
            safe_reason_code="generated",
        )


def test_complete_attempt_rejects_unapproved_diagnostic_code():
    store = object.__new__(PostgresInterviewGenerationStore)

    with pytest.raises(ValueError, match="not approved"):
        store.complete_attempt(
            "generation-1",
            1,
            "最终问题",
            lease_token="lease-token",
            fencing_version=1,
            result_mode="fallback",
            failure_reason_code="raw-provider-exception",
            provider_invocation_count=1,
            generation_latency_ms=10,
            fallback_used=True,
            safe_reason_code="raw-provider-exception",
        )


def test_failed_first_main_question_attempt_atomically_advances_retry():
    class Cursor:
        def __init__(self):
            self.calls = []
            self.rowcount = 0

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, statement, params):
            self.calls.append((" ".join(str(statement).split()), params))
            self.rowcount = 1

        def fetchone(self):
            return ("main_question",)

    class Connection:
        def __init__(self, cursor):
            self.cursor_object = cursor

        def cursor(self):
            return self.cursor_object

    cursor = Cursor()
    connection_entries = []

    @contextmanager
    def connection():
        connection_entries.append(True)
        yield Connection(cursor)

    store = object.__new__(PostgresInterviewGenerationStore)
    store._connection = connection
    store._sql = lambda statement: statement

    store.fail_attempt(
        "generation-1",
        1,
        "multiple_questions",
        lease_token="lease-token",
        fencing_version=1,
    )

    assert len(connection_entries) == 1
    assert len(cursor.calls) == 4
    assert "UPDATE {generations}" in cursor.calls[2][0]
    assert cursor.calls[2][1] == (2, "generation-1", 1)
    assert "INSERT INTO {attempts}" in cursor.calls[3][0]
    assert cursor.calls[3][1] == ("generation-1", 2)


def test_start_attempt_rejects_third_main_question_attempt():
    class Cursor:
        rowcount = 0

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, *_args):
            return None

        def fetchone(self):
            return ("pending", 2, "main_question")

    class Connection:
        def cursor(self):
            return Cursor()

    @contextmanager
    def connection():
        yield Connection()

    store = object.__new__(PostgresInterviewGenerationStore)
    store._connection = connection
    store._sql = lambda statement: statement

    with pytest.raises(GenerationLeaseConflict):
        store.start_attempt("generation-1", 3)


def test_start_attempt_preserves_third_followup_attempt():
    class Cursor:
        def __init__(self):
            self.rows = [
                ("pending", 3, "followup"),
                (
                    "generation-1",
                    3,
                    "running",
                    "worker",
                    "lease-token",
                    1,
                    "lease-expiry",
                ),
            ]
            self.rowcount = 0

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, *_args):
            self.rowcount = 1

        def fetchone(self):
            return self.rows.pop(0)

    class Connection:
        def __init__(self):
            self.cursor_object = Cursor()

        def cursor(self):
            return self.cursor_object

    @contextmanager
    def connection():
        yield Connection()

    store = object.__new__(PostgresInterviewGenerationStore)
    store._connection = connection
    store._sql = lambda statement: statement

    attempt = store.start_attempt("generation-1", 3)

    assert attempt.attempt_number == 3


def test_failed_followup_attempt_does_not_enter_main_question_retry_cas():
    class Cursor:
        def __init__(self):
            self.calls = []
            self.rowcount = 0

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, statement, params):
            self.calls.append((" ".join(str(statement).split()), params))
            self.rowcount = 1

        def fetchone(self):
            return ("followup",)

    class Connection:
        def __init__(self, cursor):
            self.cursor_object = cursor

        def cursor(self):
            return self.cursor_object

    cursor = Cursor()

    @contextmanager
    def connection():
        yield Connection(cursor)

    store = object.__new__(PostgresInterviewGenerationStore)
    store._connection = connection
    store._sql = lambda statement: statement

    store.fail_attempt(
        "generation-1",
        1,
        "provider_unavailable",
        lease_token="lease-token",
        fencing_version=1,
    )

    assert len(cursor.calls) == 2
    assert "SELECT generation_kind" in cursor.calls[0][0]
    assert "UPDATE {attempts}" in cursor.calls[1][0]


def test_failed_main_question_attempt_rolls_back_when_retry_cas_loses():
    class Cursor:
        def __init__(self):
            self.calls = []
            self.rowcount = 0

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, statement, params):
            self.calls.append((" ".join(str(statement).split()), params))
            self.rowcount = (
                0 if "UPDATE {generations}" in self.calls[-1][0] else 1
            )

        def fetchone(self):
            return ("main_question",)

    class Connection:
        def __init__(self, cursor):
            self.cursor_object = cursor

        def cursor(self):
            return self.cursor_object

    cursor = Cursor()

    @contextmanager
    def connection():
        yield Connection(cursor)

    store = object.__new__(PostgresInterviewGenerationStore)
    store._connection = connection
    store._sql = lambda statement: statement

    with pytest.raises(
        GenerationLeaseConflict,
        match="retry state changed concurrently",
    ):
        store.fail_attempt(
            "generation-1",
            1,
            "multiple_questions",
            lease_token="lease-token",
            fencing_version=1,
        )

    assert len(cursor.calls) == 3
    assert "INSERT INTO {attempts}" not in cursor.calls[-1][0]


def test_generation_transactions_lock_generation_before_attempt():
    complete_source = inspect.getsource(
        PostgresInterviewGenerationStore.complete_attempt
    )
    reclaim_source = inspect.getsource(
        PostgresInterviewGenerationStore.start_or_reclaim_attempt
    )

    assert complete_source.index(
        "SELECT generation_id FROM {generations}"
    ) < complete_source.index("UPDATE {attempts}")
    assert reclaim_source.index(
        "SELECT status, active_attempt, generation_kind"
    ) < reclaim_source.index("SELECT status, lease_owner")
    assert "FOR UPDATE OF a, g" not in reclaim_source


def test_prepare_generation_initializes_all_result_fields_as_null():
    source = " ".join(
        inspect.getsource(
            PostgresInterviewGenerationStore.prepare_generation
        ).split()
    )

    assert "NULL, NULL, NULL, NULL, NULL, NULL" in source
