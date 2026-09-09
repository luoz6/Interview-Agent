from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
from threading import Event
from uuid import uuid4

import pytest

from app.services.interview_generation_store import (
    ChunkCoalescer,
    GenerationAlreadyCompleted,
    GenerationInputConflict,
    GenerationLeaseConflict,
    PostgresInterviewGenerationStore,
)
from app.services.followup_prompts import (
    FOLLOWUP_DECISION_PROMPT_SHA256,
    FOLLOWUP_DECISION_PROMPT_VERSION,
    FOLLOWUP_GENERATION_PROMPT_SHA256,
    FOLLOWUP_GENERATION_PROMPT_VERSION,
)
from app.services.postgres_connections import DirectPsycopg2ConnectionProvider
from app.services.postgres_session import PostgresInterviewSessionStore
from app.services.workflow_thread_lock import GenerationLeaseLost
from tests.integration.postgres.test_postgres_session_store import make_plan


pytestmark = pytest.mark.pg_runtime


@pytest.fixture
def store(postgres_dsn, runtime_table_prefix):
    prefix = runtime_table_prefix
    session_store = PostgresInterviewSessionStore(
        dsn=postgres_dsn, table_prefix=prefix
    )
    turn = session_store.start(
        make_plan(),
        job_description="Backend role",
        resume_text="Built APIs",
        job_tags=["python"],
    )
    generation_store = PostgresInterviewGenerationStore(
        dsn=postgres_dsn, table_prefix=prefix
    )
    generation_store.session_id = turn.session_id
    return generation_store


def seed_generation(store):
    generation = store.prepare_generation(
        session_id=store.session_id,
        source_command_id="cmd-1",
        question_id="q1",
    )
    attempt = store.start_attempt(generation.generation_id, 1)
    return generation, attempt


def seed_main_question_generation(store, suffix="main"):
    digest = sha256(suffix.encode("utf-8")).hexdigest()
    return store.prepare_generation(
        session_id=store.session_id,
        source_command_id=f"cmd-{suffix}",
        question_id="q1",
        generation_prompt_version="main-question-generation-v1",
        generation_prompt_sha256=sha256(b"main-question-prompt").hexdigest(),
        generation_kind="main_question",
        identity_sha256=digest,
        intent_sha256=sha256(b"intent").hexdigest(),
        context_sha256=sha256(b"context").hexdigest(),
        knowledge_scope_sha256=sha256(b"knowledge").hexdigest(),
        generator_version="main-question-generator-v1",
    )


class _CursorProxy:
    def __init__(
        self,
        cursor,
        *,
        before_execute=None,
        after_execute=None,
        zero_rowcount_when=None,
    ):
        self._cursor = cursor
        self._before_execute = before_execute
        self._after_execute = after_execute
        self._zero_rowcount_when = zero_rowcount_when
        self._statement = ""

    def __enter__(self):
        self._cursor.__enter__()
        return self

    def __exit__(self, *args):
        return self._cursor.__exit__(*args)

    def execute(self, statement, params=None):
        self._statement = str(statement)
        if self._before_execute is not None:
            self._before_execute(self._statement)
        result = self._cursor.execute(statement, params)
        if self._after_execute is not None:
            self._after_execute(self._statement)
        return result

    @property
    def rowcount(self):
        if (
            self._zero_rowcount_when is not None
            and self._zero_rowcount_when(self._statement)
        ):
            return 0
        return self._cursor.rowcount

    def __getattr__(self, name):
        return getattr(self._cursor, name)


class _ConnectionProxy:
    def __init__(self, connection, **cursor_hooks):
        self._connection = connection
        self._cursor_hooks = cursor_hooks

    def cursor(self, *args, **kwargs):
        return _CursorProxy(
            self._connection.cursor(*args, **kwargs),
            **self._cursor_hooks,
        )

    def __getattr__(self, name):
        return getattr(self._connection, name)


class _InterceptingProvider:
    def __init__(self, dsn, **cursor_hooks):
        self._delegate = DirectPsycopg2ConnectionProvider(dsn)
        self._cursor_hooks = cursor_hooks

    @contextmanager
    def connection(self):
        with self._delegate.connection() as connection:
            yield _ConnectionProxy(connection, **self._cursor_hooks)


def store_with_interceptor(store, **cursor_hooks):
    return PostgresInterviewGenerationStore(
        dsn=store.dsn,
        connection_provider=_InterceptingProvider(store.dsn, **cursor_hooks),
        table_prefix=store.table_prefix,
        schema_mode="validate",
    )


def main_question_completion_update(store):
    return store._sql(
        "UPDATE {generations} SET status = 'completed', "
        "result_mode = 'generated', failure_reason_code = NULL, "
        "provider_invocation_count = %s, generation_latency_ms = %s, "
        "fallback_used = %s, safe_reason_code = %s "
        "WHERE generation_id = %s"
    )


def test_generation_is_idempotent_per_source_command(store):
    first = store.prepare_generation(
        session_id=store.session_id,
        source_command_id="cmd-1",
        question_id="q1",
    )
    second = store.prepare_generation(
        session_id=store.session_id,
        source_command_id="cmd-1",
        question_id="q1",
    )

    assert first.generation_id == second.generation_id


def test_main_question_rejects_attempt_three_but_followup_remains_compatible(store):
    main_question = seed_main_question_generation(store, "attempt-limit")

    with pytest.raises(GenerationLeaseConflict):
        store.start_attempt(main_question.generation_id, 3)

    followup = store.prepare_generation(
        session_id=store.session_id,
        source_command_id="cmd-followup-attempt-three",
        question_id="q1",
    )
    attempt = store.start_attempt(followup.generation_id, 3)

    assert attempt.attempt_number == 3
    assert store.get_by_id(followup.generation_id).active_attempt == 3


def test_complete_and_start_use_one_lock_order_without_deadlock(store):
    generation = seed_main_question_generation(store, "complete-start-race")
    attempt = store.start_attempt(generation.generation_id, 1)
    complete_has_generation_lock = Event()
    start_reached_generation_lock = Event()
    release_complete = Event()

    def pause_after_complete_lock(statement):
        if "SELECT generation_id FROM" in statement and "FOR UPDATE" in statement:
            complete_has_generation_lock.set()
            if not release_complete.wait(timeout=5):
                raise TimeoutError("test did not release the completed transaction")

    def mark_start_lock_attempt(statement):
        if (
            "SELECT status, active_attempt, generation_kind" in statement
            and "FOR UPDATE" in statement
        ):
            start_reached_generation_lock.set()

    completing_store = store_with_interceptor(
        store,
        after_execute=pause_after_complete_lock,
    )
    starting_store = store_with_interceptor(
        store,
        before_execute=mark_start_lock_attempt,
    )

    def complete():
        completing_store.complete_attempt(
            generation.generation_id,
            1,
            "最终问题",
            lease_token=attempt.lease_token,
            fencing_version=attempt.fencing_version,
            result_mode="generated",
            provider_invocation_count=1,
            generation_latency_ms=25,
            fallback_used=False,
            safe_reason_code="generated",
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        completed = executor.submit(complete)
        assert complete_has_generation_lock.wait(timeout=5)
        started = executor.submit(
            starting_store.start_attempt,
            generation.generation_id,
            2,
        )
        try:
            assert start_reached_generation_lock.wait(timeout=5)
        finally:
            release_complete.set()
        completed.result(timeout=5)
        with pytest.raises(GenerationAlreadyCompleted):
            started.result(timeout=5)

    stored = store.get_by_id(generation.generation_id)
    assert (stored.status, stored.active_attempt, stored.final_text) == (
        "completed",
        1,
        "最终问题",
    )
    with store._connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                store._sql(
                    "SELECT attempt_number, status FROM {attempts} "
                    "WHERE generation_id = %s ORDER BY attempt_number"
                ),
                (generation.generation_id,),
            )
            assert cursor.fetchall() == [(1, "completed")]


@pytest.mark.parametrize(
    "null_field",
    (
        "provider_invocation_count",
        "generation_latency_ms",
        "fallback_used",
        "safe_reason_code",
    ),
)
def test_main_question_completion_requires_each_diagnostic(store, null_field):
    from psycopg2.errors import CheckViolation

    generation = seed_main_question_generation(store, f"null-{null_field}")
    diagnostics = {
        "provider_invocation_count": 1,
        "generation_latency_ms": 10,
        "fallback_used": False,
        "safe_reason_code": "generated",
    }
    diagnostics[null_field] = None

    with pytest.raises(CheckViolation):
        with store._connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    main_question_completion_update(store),
                    (
                        diagnostics["provider_invocation_count"],
                        diagnostics["generation_latency_ms"],
                        diagnostics["fallback_used"],
                        diagnostics["safe_reason_code"],
                        generation.generation_id,
                    ),
                )

    stored = store.get_by_id(generation.generation_id)
    assert stored.status == "pending"
    assert stored.provider_invocation_count is None
    assert stored.safe_reason_code is None


def test_main_question_completion_rejects_unsafe_reason_code(store):
    from psycopg2.errors import CheckViolation

    generation = seed_main_question_generation(store, "unsafe-reason-code")
    with pytest.raises(CheckViolation):
        with store._connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    main_question_completion_update(store),
                    (1, 10, False, "raw_provider_error", generation.generation_id),
                )

    stored = store.get_by_id(generation.generation_id)
    assert stored.status == "pending"
    assert stored.provider_invocation_count is None
    assert stored.safe_reason_code is None


def test_retry_cas_failure_rolls_back_attempt_and_generation_together(store):
    generation = seed_main_question_generation(store, "retry-cas-rollback")
    attempt = store.start_attempt(generation.generation_id, 1)

    def hide_retry_cas_success(statement):
        return "SET status = 'pending', active_attempt = %s" in statement

    failing_store = store_with_interceptor(
        store,
        zero_rowcount_when=hide_retry_cas_success,
    )
    with pytest.raises(GenerationLeaseConflict, match="retry state changed"):
        failing_store.fail_attempt(
            generation.generation_id,
            1,
            "provider_timeout",
            lease_token=attempt.lease_token,
            fencing_version=attempt.fencing_version,
        )

    stored = store.get_by_id(generation.generation_id)
    assert (stored.status, stored.active_attempt) == ("running", 1)
    with store._connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                store._sql(
                    "SELECT attempt_number, status, last_error_code "
                    "FROM {attempts} WHERE generation_id = %s "
                    "ORDER BY attempt_number"
                ),
                (generation.generation_id,),
            )
            assert cursor.fetchall() == [(1, "running", None)]


def test_generation_binds_one_source_decision_and_rejects_rebinding(store):
    decision_id = str(uuid4())
    first = store.prepare_generation(
        session_id=store.session_id,
        source_command_id="command-decision-link",
        question_id="q1",
        source_decision_id=decision_id,
        decision_prompt_version=FOLLOWUP_DECISION_PROMPT_VERSION,
        decision_prompt_sha256=FOLLOWUP_DECISION_PROMPT_SHA256,
        generation_prompt_version=FOLLOWUP_GENERATION_PROMPT_VERSION,
        generation_prompt_sha256=FOLLOWUP_GENERATION_PROMPT_SHA256,
    )
    replay = store.prepare_generation(
        session_id=store.session_id,
        source_command_id="command-decision-link",
        question_id="q1",
        source_decision_id=decision_id,
        decision_prompt_version=FOLLOWUP_DECISION_PROMPT_VERSION,
        decision_prompt_sha256=FOLLOWUP_DECISION_PROMPT_SHA256,
        generation_prompt_version=FOLLOWUP_GENERATION_PROMPT_VERSION,
        generation_prompt_sha256=FOLLOWUP_GENERATION_PROMPT_SHA256,
    )

    assert first.source_decision_id == replay.source_decision_id == decision_id
    assert first.decision_prompt_version == FOLLOWUP_DECISION_PROMPT_VERSION
    assert first.decision_prompt_sha256 == FOLLOWUP_DECISION_PROMPT_SHA256
    assert first.generation_prompt_version == FOLLOWUP_GENERATION_PROMPT_VERSION
    assert first.generation_prompt_sha256 == FOLLOWUP_GENERATION_PROMPT_SHA256
    with pytest.raises(GenerationInputConflict):
        store.prepare_generation(
            session_id=store.session_id,
            source_command_id="command-decision-link",
            question_id="q1",
            source_decision_id=str(uuid4()),
            decision_prompt_version=FOLLOWUP_DECISION_PROMPT_VERSION,
            decision_prompt_sha256=FOLLOWUP_DECISION_PROMPT_SHA256,
            generation_prompt_version=FOLLOWUP_GENERATION_PROMPT_VERSION,
            generation_prompt_sha256=FOLLOWUP_GENERATION_PROMPT_SHA256,
        )
    assert first.active_attempt == 1


def test_pending_generation_prompt_lineage_rejects_non_null_old_version(store):
    decision_id = str(uuid4())
    kwargs = {
        "session_id": store.session_id,
        "source_command_id": "command-prompt-lineage",
        "question_id": "q1",
        "source_decision_id": decision_id,
        "decision_prompt_version": FOLLOWUP_DECISION_PROMPT_VERSION,
        "decision_prompt_sha256": FOLLOWUP_DECISION_PROMPT_SHA256,
        "generation_prompt_version": FOLLOWUP_GENERATION_PROMPT_VERSION,
        "generation_prompt_sha256": FOLLOWUP_GENERATION_PROMPT_SHA256,
    }
    first = store.prepare_generation(**kwargs)
    assert store.prepare_generation(**kwargs).generation_id == first.generation_id

    with pytest.raises(GenerationInputConflict, match="input conflicts"):
        store.prepare_generation(
            **{
                **kwargs,
                "generation_prompt_version": "followup-generation-v1",
                "generation_prompt_sha256": "f" * 64,
            }
        )


def test_pending_legacy_null_generation_lineage_is_not_rebound(store):
    legacy = store.prepare_generation(
        session_id=store.session_id,
        source_command_id="command-legacy-null-lineage",
        question_id="q1",
    )

    with pytest.raises(GenerationInputConflict, match="input conflicts"):
        store.prepare_generation(
            session_id=store.session_id,
            source_command_id="command-legacy-null-lineage",
            question_id="q1",
            generation_prompt_version=FOLLOWUP_GENERATION_PROMPT_VERSION,
            generation_prompt_sha256=FOLLOWUP_GENERATION_PROMPT_SHA256,
        )

    stored = store.get_by_id(legacy.generation_id)
    assert stored.generation_prompt_version is None
    assert stored.generation_prompt_sha256 is None


def test_chunks_are_ordered_and_attempt_scoped(store):
    generation, first = seed_generation(store)
    store.append_chunk(
        generation.generation_id,
        1,
        1,
        "first ",
        lease_token=first.lease_token,
        fencing_version=first.fencing_version,
    )
    store.append_chunk(
        generation.generation_id,
        1,
        2,
        "attempt",
        lease_token=first.lease_token,
        fencing_version=first.fencing_version,
    )
    store.abandon_attempt(
        generation.generation_id,
        1,
        "worker_lost",
        lease_token=first.lease_token,
        fencing_version=first.fencing_version,
    )
    second = store.start_attempt(generation.generation_id, 2)
    store.append_chunk(
        generation.generation_id,
        2,
        1,
        "replacement",
        lease_token=second.lease_token,
        fencing_version=second.fencing_version,
    )

    replay = store.list_events(generation.generation_id)

    assert [(item.attempt_number, item.sequence) for item in replay] == [
        (1, 1),
        (1, 2),
        (2, 0),
        (2, 1),
    ]
    assert replay[2].event_type == "generation_reset"


def test_completed_attempt_is_not_replaced(store):
    generation, attempt = seed_generation(store)
    store.complete_attempt(
        generation.generation_id,
        1,
        "complete",
        lease_token=attempt.lease_token,
        fencing_version=attempt.fencing_version,
    )

    with pytest.raises(GenerationAlreadyCompleted):
        store.start_attempt(generation.generation_id, 2)


def test_cleanup_removes_only_old_completed_generation_chunks(store):
    completed, completed_attempt = seed_generation(store)
    store.append_chunk(
        completed.generation_id,
        1,
        1,
        "completed",
        lease_token=completed_attempt.lease_token,
        fencing_version=completed_attempt.fencing_version,
    )
    store.complete_attempt(
        completed.generation_id,
        1,
        "completed",
        lease_token=completed_attempt.lease_token,
        fencing_version=completed_attempt.fencing_version,
    )
    with store._connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                store._sql(
                    "UPDATE {generations} SET completed_at = NOW() - INTERVAL '2 hours' WHERE generation_id = %s"
                ),
                (completed.generation_id,),
            )
    active = store.prepare_generation(
        session_id=store.session_id,
        source_command_id="cmd-active",
        question_id="q1",
    )
    active_attempt = store.start_attempt(active.generation_id, 1)
    store.append_chunk(
        active.generation_id,
        1,
        1,
        "active",
        lease_token=active_attempt.lease_token,
        fencing_version=active_attempt.fencing_version,
    )

    deleted = store.cleanup_completed_chunks(
        older_than=datetime.now(timezone.utc)
    )

    assert deleted == 1
    assert store.list_events(completed.generation_id) == []
    assert [event.delta for event in store.list_events(active.generation_id)] == [
        "active"
    ]


def test_expired_attempt_is_replaced_with_reset_event(store):
    generation, attempt = seed_generation(store)
    store.append_chunk(
        generation.generation_id,
        1,
        1,
        "partial",
        lease_token=attempt.lease_token,
        fencing_version=attempt.fencing_version,
    )
    with store._connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                store._sql(
                    "UPDATE {attempts} SET lease_expires_at = NOW() - INTERVAL '1 second' WHERE generation_id = %s AND attempt_number = 1"
                ),
                (generation.generation_id,),
            )

    replacement = store.start_or_reclaim_attempt(
        generation.generation_id,
        1,
        worker_id="replacement-worker",
        lease_seconds=60,
    )

    assert replacement.attempt_number == 2
    events = store.list_events(generation.generation_id)
    assert [(event.attempt_number, event.event_type) for event in events] == [
        (1, "chunk"),
        (2, "generation_reset"),
    ]


def test_reclaimed_attempt_rejects_every_stale_mutation(store):
    generation, first = seed_generation(store)
    store.fail_attempt(
        generation.generation_id,
        1,
        "provider_timeout",
        lease_token=first.lease_token,
        fencing_version=first.fencing_version,
    )
    current = store.start_attempt(
        generation.generation_id,
        1,
        worker_id="replacement-worker",
    )

    assert current.lease_token != first.lease_token
    assert current.fencing_version > first.fencing_version
    assert store.assert_attempt_owned(
        generation.generation_id,
        1,
        "worker",
        lease_token=first.lease_token,
        fencing_version=first.fencing_version,
    ) is False
    assert store.assert_attempt_owned(
        generation.generation_id,
        1,
        "replacement-worker",
        lease_token=current.lease_token,
        fencing_version=current.fencing_version,
    ) is True
    assert store.heartbeat_attempt(
        generation.generation_id,
        1,
        "worker",
        lease_token=first.lease_token,
        fencing_version=first.fencing_version,
    ) is False
    with pytest.raises(GenerationLeaseLost):
        store.append_chunk(
            generation.generation_id,
            1,
            1,
            "stale",
            lease_token=first.lease_token,
            fencing_version=first.fencing_version,
        )
    with pytest.raises(GenerationLeaseLost):
        store.fail_attempt(
            generation.generation_id,
            1,
            "stale_failure",
            lease_token=first.lease_token,
            fencing_version=first.fencing_version,
        )
    with pytest.raises(GenerationLeaseLost):
        store.abandon_attempt(
            generation.generation_id,
            1,
            "stale_abandon",
            lease_token=first.lease_token,
            fencing_version=first.fencing_version,
        )
    with pytest.raises(GenerationLeaseLost):
        store.complete_attempt(
            generation.generation_id,
            1,
            "stale complete",
            lease_token=first.lease_token,
            fencing_version=first.fencing_version,
        )

    store.complete_attempt(
        generation.generation_id,
        1,
        "winner",
        lease_token=current.lease_token,
        fencing_version=current.fencing_version,
    )
    assert store.get_by_id(generation.generation_id).final_text == "winner"


def test_attempt_schema_has_token_and_fencing_columns(store):
    with store._connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT column_name, data_type, is_nullable, column_default
                FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = %s
                  AND column_name IN ('lease_token', 'fencing_version')
                """,
                (store.attempts_table,),
            )
            columns = {row[0]: row[1:] for row in cursor.fetchall()}

    assert columns["lease_token"][0] == "uuid"
    assert columns["fencing_version"][0] == "bigint"
    assert columns["fencing_version"][1] == "NO"


def test_redundant_generation_indexes_are_absent(store):
    with store._connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT indexname, indexdef
                FROM pg_indexes
                WHERE schemaname = current_schema()
                  AND tablename = ANY(%s)
                """,
                ([store.generations_table, store.chunks_table],),
            )
            indexes = cursor.fetchall()

    generation_pair = [
        definition
        for _, definition in indexes
        if "(session_id, source_command_id)" in definition
    ]
    chunk_order = [
        definition
        for _, definition in indexes
        if "(generation_id, attempt_number, sequence)" in definition
    ]
    assert len(generation_pair) == 1
    assert "UNIQUE INDEX" in generation_pair[0]
    assert len(chunk_order) == 1
    assert "UNIQUE INDEX" in chunk_order[0]
