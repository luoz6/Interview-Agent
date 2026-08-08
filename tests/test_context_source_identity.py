from __future__ import annotations

from dataclasses import replace

import pytest

from app.services.context_source_identity import (
    CONTEXT_SOURCE_IDENTITY_SCHEMA_VERSION,
    SOURCE_REPRESENTATION_IDENTITY_SCHEMA_VERSION,
    ConversationSourceIdentity,
    EvidenceSourceIdentity,
    SourceRepresentationIdentity,
    canonical_conversation_sequence_pair,
    content_sha256,
    source_value_sha256,
)


@pytest.mark.parametrize(
    ("sequence_no", "sequence_contract", "expected"),
    (
        (9, "authoritative-v1", (9, "authoritative-v1")),
        (9, None, (4, "state-order-v1")),
        (None, "authoritative-v1", (4, "state-order-v1")),
        (None, None, (4, "state-order-v1")),
    ),
)
def test_conversation_sequence_pair_is_canonical_and_indivisible(
    sequence_no,
    sequence_contract,
    expected,
):
    pair = canonical_conversation_sequence_pair(
        sequence_no=sequence_no,
        sequence_contract=sequence_contract,
        state_position=4,
    )
    identity = ConversationSourceIdentity(
        owner_scope="interview-session:session-1",
        question_id="question-1",
        sequence_no=pair[0],
        sequence_contract=pair[1],
        role="candidate",
        content_sha256=content_sha256("answer"),
    )
    replay_pair = canonical_conversation_sequence_pair(
        sequence_no=pair[0],
        sequence_contract=pair[1],
        state_position=99,
    )
    replay = replace(
        identity,
        sequence_no=replay_pair[0],
        sequence_contract=replay_pair[1],
    )

    assert pair == expected
    assert replay_pair == expected
    assert identity.canonical_json.encode("utf-8") == replay.canonical_json.encode(
        "utf-8"
    )
    assert identity.sha256 == replay.sha256


@pytest.mark.parametrize(
    ("sequence_no", "sequence_contract", "state_position"),
    (
        (0, "authoritative-v1", 1),
        (1, "unknown-v1", 1),
        (1, "authoritative-v1", 0),
    ),
)
def test_complete_malformed_sequence_pair_is_not_relabelled_as_state_order(
    sequence_no,
    sequence_contract,
    state_position,
):
    with pytest.raises(ValueError):
        canonical_conversation_sequence_pair(
            sequence_no=sequence_no,
            sequence_contract=sequence_contract,
            state_position=state_position,
        )


def _conversation() -> ConversationSourceIdentity:
    return ConversationSourceIdentity(
        owner_scope="interview-session:session-1",
        question_id="question-1",
        sequence_no=7,
        sequence_contract="authoritative-v1",
        role="candidate",
        content_sha256=content_sha256("same answer"),
    )


def _evidence() -> EvidenceSourceIdentity:
    return EvidenceSourceIdentity(
        owner_scope="interview-session:session-1",
        provenance="knowledge-corpus:theory",
        chunk_or_evidence_id_sha256=source_value_sha256("chunk-1"),
        content_sha256=content_sha256("same evidence"),
        corpus_manifest_sha256="a" * 64,
    )


def test_conversation_identity_is_canonical_versioned_and_replay_stable():
    first = _conversation()
    replay = _conversation()

    assert first == replay
    assert first.schema_version == CONTEXT_SOURCE_IDENTITY_SCHEMA_VERSION
    assert first.canonical_json == replay.canonical_json
    assert first.sha256 == replay.sha256
    assert len(first.sha256) == 64


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("owner_scope", "interview-session:session-2"),
        ("question_id", "question-2"),
        ("sequence_no", 8),
        ("sequence_contract", "state-order-v1"),
        ("role", "interviewer"),
        ("content_sha256", content_sha256("changed answer")),
    ),
)
def test_every_conversation_contract_field_changes_identity(field, value):
    original = _conversation()
    changed = replace(original, **{field: value})

    assert changed.sha256 != original.sha256


def test_evidence_identity_is_canonical_versioned_and_replay_stable():
    first = _evidence()
    replay = _evidence()

    assert first == replay
    assert first.schema_version == CONTEXT_SOURCE_IDENTITY_SCHEMA_VERSION
    assert first.canonical_json == replay.canonical_json
    assert first.sha256 == replay.sha256


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("owner_scope", "interview-session:session-2"),
        ("provenance", "knowledge-corpus:benchmark"),
        ("chunk_or_evidence_id_sha256", source_value_sha256("chunk-2")),
        ("content_sha256", content_sha256("changed evidence")),
        ("corpus_manifest_sha256", "b" * 64),
    ),
)
def test_every_evidence_contract_field_changes_identity(field, value):
    original = _evidence()
    changed = replace(original, **{field: value})

    assert changed.sha256 != original.sha256


@pytest.mark.parametrize(
    "kwargs",
    (
        {"owner_scope": ""},
        {"question_id": ""},
        {"sequence_no": 0},
        {"sequence_no": True},
        {"sequence_contract": "unknown-v1"},
        {"role": "knowledge_evidence"},
        {"content_sha256": "not-a-digest"},
        {"schema_version": "context-source-identity-v0"},
    ),
)
def test_malformed_conversation_identity_fails_closed(kwargs):
    values = {
        "owner_scope": "interview-session:session-1",
        "question_id": "question-1",
        "sequence_no": 7,
        "sequence_contract": "authoritative-v1",
        "role": "candidate",
        "content_sha256": content_sha256("answer"),
    }
    values.update(kwargs)

    with pytest.raises((TypeError, ValueError)):
        ConversationSourceIdentity(**values)


@pytest.mark.parametrize(
    "kwargs",
    (
        {"owner_scope": ""},
        {"provenance": ""},
        {"chunk_or_evidence_id_sha256": "not-a-digest"},
        {"content_sha256": "not-a-digest"},
        {"corpus_manifest_sha256": "not-a-digest"},
        {"role": "candidate"},
        {"schema_version": "context-source-identity-v0"},
    ),
)
def test_malformed_evidence_identity_fails_closed(kwargs):
    values = {
        "owner_scope": "interview-session:session-1",
        "provenance": "knowledge-corpus:theory",
        "chunk_or_evidence_id_sha256": source_value_sha256("chunk-1"),
        "content_sha256": content_sha256("evidence"),
        "corpus_manifest_sha256": "a" * 64,
    }
    values.update(kwargs)

    with pytest.raises((TypeError, ValueError)):
        EvidenceSourceIdentity(**values)


def test_missing_required_identity_fields_fail_closed():
    with pytest.raises(TypeError):
        ConversationSourceIdentity(
            owner_scope="interview-session:session-1",
            question_id="question-1",
            sequence_no=1,
            sequence_contract="authoritative-v1",
            role="candidate",
        )
    with pytest.raises(TypeError):
        EvidenceSourceIdentity(
            owner_scope="interview-session:session-1",
            provenance="knowledge-corpus:theory",
            content_sha256=content_sha256("evidence"),
            corpus_manifest_sha256="a" * 64,
        )


def test_representation_identity_requires_source_and_representation_equivalence():
    source = _conversation()
    first = SourceRepresentationIdentity(
        source_identity_sha256=source.sha256,
        role="candidate",
        representation="authoritative_raw",
        content_sha256=source.content_sha256,
    )
    replay = SourceRepresentationIdentity(
        source_identity_sha256=source.sha256,
        role="candidate",
        representation="authoritative_raw",
        content_sha256=source.content_sha256,
    )

    assert first.schema_version == SOURCE_REPRESENTATION_IDENTITY_SCHEMA_VERSION
    assert first.sha256 == replay.sha256
    assert replace(first, representation="compressed_projection").sha256 != first.sha256
    assert replace(first, content_sha256=content_sha256("bounded")).sha256 != first.sha256


@pytest.mark.parametrize("value", (None, "", "source\x00scope", 42))
def test_source_values_are_strict_nonempty_utf8_strings(value):
    with pytest.raises((TypeError, ValueError)):
        source_value_sha256(value)  # type: ignore[arg-type]
