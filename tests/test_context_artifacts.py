from hashlib import sha256

import pytest
from pydantic import ValidationError

from app.services.context_artifacts import (
    AnchoredCompressedUnit,
    CompressionSourceSegment,
    EvidenceCompressionArtifact,
    ContextArtifactRef,
    QuestionConversationArtifact,
    QuestionMemoryArtifact,
    build_question_memory_source_manifest,
    artifact_payload_sha256,
    canonical_json,
    parse_artifact_payload,
)
from app.services.context_source_identity import ConversationSourceIdentity


def test_source_segment_requires_its_authoritative_content_digest():
    content = "Candidate explained transactional outbox ordering."
    segment = CompressionSourceSegment(
        segment_index=0,
        segment_type="conversation_message",
        content=content,
        content_sha256=sha256(content.encode("utf-8")).hexdigest(),
    )
    assert segment.content == content

    with pytest.raises(ValidationError, match="content_sha256"):
        CompressionSourceSegment(
            segment_index=0,
            segment_type="conversation_message",
            content=content,
            content_sha256="0" * 64,
        )


def test_artifact_schemas_forbid_extra_provider_fields():
    with pytest.raises(ValidationError, match="extra_forbidden"):
        QuestionConversationArtifact.model_validate(
            {
                "schema_version": "question-conversation-v1",
                "question_id_sha256": "1" * 64,
                "units": [],
                "source_message_count": 1,
                "raw_answer": "must not persist",
            }
        )


def test_artifact_payload_digest_is_stable_after_schema_validation():
    payload = EvidenceCompressionArtifact(
        schema_version="evidence-compression-v1",
        evidence_content_sha256="1" * 64,
        units=[
            AnchoredCompressedUnit(
                summary="Use an idempotency key for retry safety.",
                source_segment_sha256=["2" * 64],
                supporting_excerpts=["idempotency key"],
            )
        ],
        exact_excerpts=["idempotency key"],
    )
    dumped = payload.model_dump(mode="json")

    assert parse_artifact_payload("evidence_compression", dumped) == payload
    assert artifact_payload_sha256(payload) == artifact_payload_sha256(dumped)
    assert dumped == payload.model_dump(mode="json")


def test_payload_parser_rejects_schema_for_the_wrong_artifact_type():
    payload = {
        "schema_version": "question-conversation-v1",
        "question_id_sha256": "1" * 64,
        "units": [],
        "source_message_count": 1,
    }

    with pytest.raises(ValidationError):
        parse_artifact_payload("evidence_compression", payload)


def test_artifact_text_models_reject_nul_content():
    content = "unsafe\x00content"
    with pytest.raises(ValidationError, match="NUL"):
        CompressionSourceSegment(
            segment_index=0,
            segment_type="conversation_message",
            content=content,
            content_sha256=sha256(content.encode("utf-8")).hexdigest(),
        )

    with pytest.raises(ValidationError, match="NUL"):
        AnchoredCompressedUnit(
            summary="unsafe\x00summary",
            source_segment_sha256=["2" * 64],
        )


def test_question_memory_payload_is_explicitly_non_authoritative_and_grounded():
    payload = QuestionMemoryArtifact.model_validate(
        {
            "schema_version": "question-memory-v1",
            "authority": "non_authoritative",
            "session_scope_sha256": "1" * 64,
            "question_id_sha256": "2" * 64,
            "question_focus_sha256": "3" * 64,
            "source_manifest_sha256": "4" * 64,
            "source_message_count": 2,
            "claims": [
                {
                    "claim_type": "tradeoff",
                    "summary": "Candidate discussed a cache tradeoff.",
                    "polarity": "positive",
                    "source_segment_sha256": ["5" * 64],
                    "supporting_excerpts": ["cache tradeoff"],
                    "confidence": "medium",
                }
            ],
            "unresolved_topics": [],
        }
    )

    assert payload.authority == "non_authoritative"
    assert parse_artifact_payload("question_memory", payload) == payload
    with pytest.raises(ValidationError):
        parse_artifact_payload("evidence_compression", payload)


def test_question_memory_claim_requires_exact_excerpt_and_closed_enums():
    base = {
        "schema_version": "question-memory-v1",
        "authority": "non_authoritative",
        "session_scope_sha256": "1" * 64,
        "question_id_sha256": "2" * 64,
        "question_focus_sha256": "3" * 64,
        "source_manifest_sha256": "4" * 64,
        "source_message_count": 1,
        "claims": [
            {
                "claim_type": "skill",
                "summary": "Candidate described a skill.",
                "polarity": "positive",
                "source_segment_sha256": ["5" * 64],
                "supporting_excerpts": [],
                "confidence": "high",
            }
        ],
        "unresolved_topics": [],
    }

    with pytest.raises(ValidationError, match="too_short"):
        QuestionMemoryArtifact.model_validate(base)
    base["claims"][0]["supporting_excerpts"] = ["skill"]
    base["claims"][0]["polarity"] = "invented"
    with pytest.raises(ValidationError):
        QuestionMemoryArtifact.model_validate(base)


def test_ordered_question_memory_source_manifest_hashes_no_content():
    messages = [
        {
            "sequence_no": 2,
            "role": "candidate",
            "question_id": "q1",
            "content": "secret candidate answer",
        },
        {
            "sequence_no": 1,
            "role": "interviewer",
            "question_id": "q1",
            "content": "secret interview question",
        },
    ]

    manifest = build_question_memory_source_manifest(messages)

    expected_items = (
        {
            "sequence_no": 1,
            "role": "interviewer",
            "question_id_sha256": sha256(b"q1").hexdigest(),
            "content_sha256": sha256(
                b"secret interview question"
            ).hexdigest(),
        },
        {
            "sequence_no": 2,
            "role": "candidate",
            "question_id_sha256": sha256(b"q1").hexdigest(),
            "content_sha256": sha256(b"secret candidate answer").hexdigest(),
        },
    )
    legacy_payload = (
        '[{"content_sha256":"cd97a4161d30cc8398dcb202b78c69949c4f03f210ed695'
        'bee6e4b982bf62872","question_id_sha256":"c75de8c1b7c3ae5252091267a736a9'
        'bf57001d80e82668b3cb3cd09e2f6a43cb","role":"interviewer","sequence_no":1},'
        '{"content_sha256":"e43d9bb756039203914d97263391c1e913f67a4d9b39aeddf1644'
        '10346b3e534","question_id_sha256":"c75de8c1b7c3ae5252091267a736a9bf57'
        '001d80e82668b3cb3cd09e2f6a43cb","role":"candidate","sequence_no":2}]'
    )

    assert manifest.items == expected_items
    assert canonical_json(list(manifest.items)).encode("utf-8") == (
        legacy_payload.encode("utf-8")
    )
    assert manifest.sha256 == (
        "2d04d0dd32ae66f643ee05119b1db0ce8d29ce57acc1b1d51bd66db8b34958c3"
    )
    assert "secret" not in repr(manifest)
    with pytest.raises(ValueError, match="unique and positive"):
        build_question_memory_source_manifest(
            [messages[0], {**messages[1], "sequence_no": 2}]
        )


def _identity_aware_manifest_message(
    *,
    sequence_no,
    sequence_contract,
    role,
    content,
):
    content_digest = sha256(content.encode("utf-8")).hexdigest()
    return {
        "sequence_no": sequence_no,
        "sequence_contract": sequence_contract,
        "role": role,
        "question_id": "q1",
        "content": content,
        "source_identity_sha256": ConversationSourceIdentity(
            owner_scope="interview-session:session-1",
            question_id="q1",
            sequence_no=sequence_no,
            sequence_contract=sequence_contract,
            role=role,
            content_sha256=content_digest,
        ).sha256,
    }


def test_identity_aware_question_memory_manifest_binds_order_and_contract():
    messages = [
        _identity_aware_manifest_message(
            sequence_no=1,
            sequence_contract="state-order-v1",
            role="interviewer",
            content="question",
        ),
        _identity_aware_manifest_message(
            sequence_no=2,
            sequence_contract="state-order-v1",
            role="candidate",
            content="answer",
        ),
    ]

    manifest = build_question_memory_source_manifest(list(reversed(messages)))
    same_identity_manifest = build_question_memory_source_manifest(messages)
    contract_changed = [
        messages[0],
        _identity_aware_manifest_message(
            sequence_no=2,
            sequence_contract="authoritative-v1",
            role="candidate",
            content="answer",
        ),
    ]
    sequence_changed = [
        _identity_aware_manifest_message(
            sequence_no=2,
            sequence_contract="state-order-v1",
            role="interviewer",
            content="question",
        ),
        _identity_aware_manifest_message(
            sequence_no=1,
            sequence_contract="state-order-v1",
            role="candidate",
            content="answer",
        ),
    ]

    assert manifest == same_identity_manifest
    assert [item["source_identity_sha256"] for item in manifest.items] == [
        message["source_identity_sha256"] for message in messages
    ]
    assert build_question_memory_source_manifest(contract_changed).sha256 != (
        manifest.sha256
    )
    assert build_question_memory_source_manifest(sequence_changed).sha256 != (
        manifest.sha256
    )


def test_question_memory_manifest_rejects_mixed_or_duplicate_source_identities():
    legacy = {
        "sequence_no": 1,
        "role": "interviewer",
        "question_id": "q1",
        "content": "question",
    }
    identified = {
        "sequence_no": 2,
        "role": "candidate",
        "question_id": "q1",
        "content": "answer",
        "source_identity_sha256": "1" * 64,
    }

    with pytest.raises(ValueError, match="all or no messages"):
        build_question_memory_source_manifest([legacy, identified])
    with pytest.raises(ValueError, match="must be unique"):
        build_question_memory_source_manifest(
            [
                {**legacy, "source_identity_sha256": "1" * 64},
                identified,
            ]
        )


@pytest.mark.parametrize(
    "digest",
    ("A" * 64, "a" * 63, "g" * 64, None, 123),
)
def test_question_memory_manifest_rejects_invalid_source_identity(digest):
    with pytest.raises(ValueError, match="lowercase SHA-256"):
        build_question_memory_source_manifest(
            [
                {
                    "sequence_no": 1,
                    "role": "interviewer",
                    "question_id": "q1",
                    "content": "question",
                    "source_identity_sha256": digest,
                }
            ]
        )


def test_artifact_ref_accepts_only_the_opaque_store_format():
    with pytest.raises(ValidationError, match="string_pattern_mismatch"):
        ContextArtifactRef(
            artifact_ref="../../candidate-resume.txt",
            artifact_sha256="1" * 64,
            artifact_type="question_conversation",
            compression_policy_version="conversation-v1",
        )
