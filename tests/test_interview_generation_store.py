from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.services.interview_generation_store import (
    ChunkCoalescer,
    GenerationAlreadyCompleted,
    PostgresInterviewGenerationStore,
)
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
    store.start_attempt(generation.generation_id, 1)
    return generation


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
    assert first.active_attempt == 1


def test_chunks_are_ordered_and_attempt_scoped(store):
    generation = seed_generation(store)
    store.append_chunk(generation.generation_id, 1, 1, "first ")
    store.append_chunk(generation.generation_id, 1, 2, "attempt")
    store.abandon_attempt(generation.generation_id, 1, "worker_lost")
    store.start_attempt(generation.generation_id, 2)
    store.append_chunk(generation.generation_id, 2, 1, "replacement")

    replay = store.list_events(generation.generation_id)

    assert [(item.attempt_number, item.sequence) for item in replay] == [
        (1, 1),
        (1, 2),
        (2, 0),
        (2, 1),
    ]
    assert replay[2].event_type == "generation_reset"


def test_completed_attempt_is_not_replaced(store):
    generation = seed_generation(store)
    store.complete_attempt(generation.generation_id, 1, "complete")

    with pytest.raises(GenerationAlreadyCompleted):
        store.start_attempt(generation.generation_id, 2)


def test_cleanup_removes_only_old_completed_generation_chunks(store):
    completed = seed_generation(store)
    store.append_chunk(completed.generation_id, 1, 1, "completed")
    store.complete_attempt(completed.generation_id, 1, "completed")
    active = store.prepare_generation(
        session_id=store.session_id,
        source_command_id="cmd-active",
        question_id="q1",
    )
    store.start_attempt(active.generation_id, 1)
    store.append_chunk(active.generation_id, 1, 1, "active")

    deleted = store.cleanup_completed_chunks(
        older_than=datetime.now(timezone.utc) + timedelta(seconds=1)
    )

    assert deleted == 1
    assert store.list_events(completed.generation_id) == []
    assert [event.delta for event in store.list_events(active.generation_id)] == [
        "active"
    ]


def test_chunk_coalescer_uses_injected_clock():
    now = [0.0]
    coalescer = ChunkCoalescer(
        max_interval_seconds=0.2, clock=lambda: now[0]
    )

    assert coalescer.add("a") is None
    now[0] = 0.3
    assert coalescer.add("b") == "ab"
    assert coalescer.flush() is None
