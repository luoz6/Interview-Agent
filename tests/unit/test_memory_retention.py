from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.services.memory_retention import (
    InMemorySessionCapacityExceeded,
    InMemorySessionRetentionPolicy,
)
from app.services.session import InterviewSessionStore
from tests.session_fixtures import FakeInterviewLLM, make_interview_plan


class Clock:
    def __init__(self):
        self.value = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def __call__(self):
        return self.value

    def advance(self, **kwargs):
        self.value += timedelta(**kwargs)


def start(store, session_id):
    return store.start(
        make_interview_plan(),
        job_description="Backend role",
        resume_text="Backend resume",
        job_tags=[],
        session_id=session_id,
    )


def test_finished_ttl_evicts_session_report_and_evaluations_together():
    clock = Clock()
    store = InterviewSessionStore(
        llm=FakeInterviewLLM(),
        clock=clock,
        retention_policy=InMemorySessionRetentionPolicy(
            max_sessions=10,
            finished_ttl_seconds=60,
            cleanup_batch_size=10,
        ),
    )
    start(store, "old")
    store.finish("old")
    store._sessions["old"]["finished_at"] = clock().isoformat()
    store._reports["old"] = object()
    store._question_evaluations["old"] = [object()]
    clock.advance(seconds=61)

    assert store.cleanup_retention() == 1
    with pytest.raises(ValueError, match="session not found"):
        store.get("old")
    assert "old" not in store._reports
    assert "old" not in store._question_evaluations


def test_capacity_never_evicts_active_sessions():
    store = InterviewSessionStore(
        llm=FakeInterviewLLM(),
        retention_policy=InMemorySessionRetentionPolicy(
            max_sessions=1,
            finished_ttl_seconds=60,
            cleanup_batch_size=1,
        ),
    )
    start(store, "active")

    with pytest.raises(InMemorySessionCapacityExceeded, match="capacity"):
        start(store, "second")

    assert store.get("active")["status"] == "active"


def test_capacity_evicts_oldest_finished_session_before_rejecting():
    clock = Clock()
    store = InterviewSessionStore(
        llm=FakeInterviewLLM(),
        clock=clock,
        retention_policy=InMemorySessionRetentionPolicy(
            max_sessions=2,
            finished_ttl_seconds=3600,
            cleanup_batch_size=1,
        ),
    )
    start(store, "finished")
    store.finish("finished")
    store._sessions["finished"]["finished_at"] = clock().isoformat()
    start(store, "active")

    start(store, "replacement")

    assert set(store._sessions) == {"active", "replacement"}


def test_cleanup_is_bounded_and_idempotent():
    clock = Clock()
    store = InterviewSessionStore(
        llm=FakeInterviewLLM(),
        clock=clock,
        retention_policy=InMemorySessionRetentionPolicy(
            max_sessions=10,
            finished_ttl_seconds=1,
            cleanup_batch_size=1,
        ),
    )
    for session_id in ("one", "two"):
        start(store, session_id)
        store.finish(session_id)
        store._sessions[session_id]["finished_at"] = clock().isoformat()
    clock.advance(seconds=2)

    assert store.cleanup_retention() == 1
    assert store.cleanup_retention() == 1
    assert store.cleanup_retention() == 0
