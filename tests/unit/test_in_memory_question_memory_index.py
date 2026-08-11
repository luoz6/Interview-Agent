from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.services.in_memory_question_memory_index import (
    InMemoryQuestionMemoryIndexStore,
)
from tests.contracts.test_question_memory_index_contracts import make_entry


class Clock:
    def __init__(self):
        self.value = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def __call__(self):
        return self.value

    def advance(self):
        self.value += timedelta(seconds=1)


def test_activation_atomically_supersedes_direct_predecessor():
    clock = Clock()
    store = InMemoryQuestionMemoryIndexStore(clock=clock)
    first = store.activate(make_entry(created_at=clock()))
    clock.advance()
    second = store.activate(
        make_entry(
            artifact_ref="context-artifact-ref:memory-2",
            artifact_sha256="4" * 64,
            source_manifest_sha256="5" * 64,
            source_message_count=3,
            source_max_sequence_no=5,
            created_at=clock(),
        )
    )

    assert second.supersedes_artifact_ref == first.artifact_ref
    assert store.get_active(
        session_id="session-1",
        question_id="q1",
        policy_version="question-memory-v1",
    ) == second
    historical = store.get_historical(first.artifact_ref)
    assert historical.status == "superseded"
    assert historical.superseded_at == clock()


def test_active_listing_is_bounded_and_ignores_superseded_entries():
    store = InMemoryQuestionMemoryIndexStore()
    store.activate(make_entry())
    store.activate(
        make_entry(
            artifact_ref="context-artifact-ref:memory-2",
            source_manifest_sha256="4" * 64,
        )
    )
    store.activate(
        make_entry(
            question_id="q2",
            artifact_ref="context-artifact-ref:memory-q2",
            source_max_sequence_no=8,
        )
    )

    items = store.list_active(
        session_id="session-1",
        policy_version="question-memory-v1",
        limit=1,
    )

    assert [item.question_id for item in items] == ["q2"]


def test_session_deletion_hides_entries_then_purge_removes_history():
    store = InMemoryQuestionMemoryIndexStore()
    entry = store.activate(make_entry())

    assert store.mark_session_deleted("session-1") == 1
    assert store.get_active(
        session_id="session-1",
        question_id="q1",
        policy_version="question-memory-v1",
    ) is None
    assert store.list_active(
        session_id="session-1",
        policy_version="question-memory-v1",
        limit=5,
    ) == []
    assert store.get_historical(entry.artifact_ref).status == "deleted"
    assert store.delete_session("session-1") == 1
    assert store.get_historical(entry.artifact_ref) is None


def test_deleted_session_rejects_future_activation():
    store = InMemoryQuestionMemoryIndexStore()
    store.mark_session_deleted("session-1")

    with pytest.raises(ValueError, match="session is deleted"):
        store.activate(make_entry())
