from datetime import datetime, timezone
from hashlib import sha256

import pytest
from pydantic import ValidationError

from app.ports.question_memory import QuestionMemoryIndexStore
from app.services.in_memory_question_memory_index import (
    InMemoryQuestionMemoryIndexStore,
)
from app.services.question_memory_index import QuestionMemoryIndexEntry


def make_entry(**changes):
    question_id = changes.pop("question_id", "q1")
    skill_tags = changes.pop("skill_tags", ["idempotency"])
    topics = changes.pop("unresolved_topic_codes", ["missing_tradeoff"])
    values = {
        "session_id": "session-1",
        "question_id": question_id,
        "question_id_sha256": sha256(question_id.encode()).hexdigest(),
        "focus_sha256": "1" * 64,
        "focus_tags": ["distributed_systems"],
        "skill_tags": skill_tags,
        "skill_tag_sha256": [sha256(value.encode()).hexdigest() for value in skill_tags],
        "unresolved_topic_codes": topics,
        "unresolved_topic_sha256": [sha256(value.encode()).hexdigest() for value in topics],
        "artifact_ref": "context-artifact-ref:memory-1",
        "artifact_sha256": "2" * 64,
        "policy_version": "question-memory-v1",
        "source_manifest_sha256": "3" * 64,
        "source_message_count": 2,
        "source_max_sequence_no": 4,
        "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
    }
    values.update(changes)
    return QuestionMemoryIndexEntry(**values)


def test_in_memory_store_satisfies_question_memory_port():
    assert isinstance(InMemoryQuestionMemoryIndexStore(), QuestionMemoryIndexStore)


def test_index_rejects_free_text_taxonomy_and_digest_mismatch():
    with pytest.raises(ValidationError, match="free text"):
        make_entry(skill_tags=["candidate said they are great"])
    with pytest.raises(ValidationError, match="digests do not match"):
        make_entry(skill_tag_sha256=["9" * 64])


def test_index_contains_no_summary_or_excerpt_fields():
    entry = make_entry()

    assert "summary" not in type(entry).model_fields
    assert "excerpt" not in repr(type(entry).model_fields).lower()
