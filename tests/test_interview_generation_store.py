from uuid import uuid4

import pytest

from app.services.interview_generation_store import (
    ChunkCoalescer,
    GenerationInputConflict,
    GenerationAlreadyCompleted,
    PostgresInterviewGenerationStore,
)
from app.services.followup_prompts import (
    FOLLOWUP_DECISION_PROMPT_SHA256,
    FOLLOWUP_DECISION_PROMPT_VERSION,
    FOLLOWUP_GENERATION_PROMPT_SHA256,
    FOLLOWUP_GENERATION_PROMPT_VERSION,
)
from app.services.workflow_thread_lock import GenerationLeaseLost
from app.services.postgres_session import PostgresInterviewSessionStore
from tests.test_postgres_session_store import make_plan, require_dsn


@pytest.fixture
def store():
    prefix = f"test_generation_{uuid4().hex[:12]}"
    session_store = PostgresInterviewSessionStore(
        dsn=require_dsn(), table_prefix=prefix
    )
    turn = session_store.start(
        make_plan(),
        job_description="Backend role",
        resume_text="Built APIs",
        job_tags=["python"],
    )
    generation_store = PostgresInterviewGenerationStore(
        dsn=require_dsn(), table_prefix=prefix
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


def test_generation_prompt_lineage_rejects_non_null_drift(store):
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
                "generation_prompt_version": "followup-generation-v2",
                "generation_prompt_sha256": "f" * 64,
            }
        )


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

    deleted = store.cleanup_completed_chunks_older_than(hours=1)

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


def test_chunk_coalescer_uses_injected_clock():
    now = [0.0]
    coalescer = ChunkCoalescer(
        max_interval_seconds=0.2, clock=lambda: now[0]
    )

    assert coalescer.add("a") is None
    now[0] = 0.3
    assert coalescer.add("b") == "ab"
    assert coalescer.flush() is None
