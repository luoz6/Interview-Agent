from hashlib import sha256

import pytest
from pydantic import ValidationError

from app.services.context_artifacts import (
    AnchoredCompressedUnit,
    CompressionSourceSegment,
    EvidenceCompressionArtifact,
    ContextArtifactRef,
    QuestionConversationArtifact,
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


def test_artifact_ref_accepts_only_the_opaque_store_format():
    with pytest.raises(ValidationError, match="string_pattern_mismatch"):
        ContextArtifactRef(
            artifact_ref="../../candidate-resume.txt",
            artifact_sha256="1" * 64,
            artifact_type="question_conversation",
            compression_policy_version="conversation-v1",
        )
