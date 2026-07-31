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
    parse_artifact_payload,
)


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

    assert [item["sequence_no"] for item in manifest.items] == [1, 2]
    assert len(manifest.sha256) == 64
    assert "secret" not in repr(manifest)
    with pytest.raises(ValueError, match="unique and positive"):
        build_question_memory_source_manifest(
            [messages[0], {**messages[1], "sequence_no": 2}]
        )


def test_artifact_ref_accepts_only_the_opaque_store_format():
    with pytest.raises(ValidationError, match="string_pattern_mismatch"):
        ContextArtifactRef(
            artifact_ref="../../candidate-resume.txt",
            artifact_sha256="1" * 64,
            artifact_type="question_conversation",
            compression_policy_version="conversation-v1",
        )
