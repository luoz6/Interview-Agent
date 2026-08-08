from copy import deepcopy
from hashlib import sha256

import pytest

from app.services.context_artifacts import (
    CompressionSourceSegment,
    ContextArtifactValidationFailed,
    ContextCompressionPolicy,
)
from app.services.context_compression_validation import (
    validate_compression_artifact,
)
from app.services.context_compression_intent import CompressionIntent


class CharacterEstimator:
    def estimate_text(self, text, *, model):
        del model
        return len(text)

    def estimate_messages(self, messages, *, model):
        return self.estimate_text(str(messages), model=model)


def make_segment(content="Use idempotency_key with retry count 3."):
    return CompressionSourceSegment(
        segment_index=0,
        segment_type="conversation_message",
        content=content,
        content_sha256=sha256(content.encode("utf-8")).hexdigest(),
    )


def make_policy(**changes):
    values = {
        "artifact_type": "question_conversation",
        "policy_version": "conversation-v1",
        "prompt_contract_version": "prompt-v1",
        "output_schema_version": "question-conversation-v1",
        "compressor_operation": "context_compressor.question_conversation",
        "compressor_input_cap_tokens": 2000,
        "target_output_tokens": 500,
        "max_output_units": 4,
        "max_supporting_excerpt_tokens": 100,
    }
    values.update(changes)
    return ContextCompressionPolicy(**values)


def make_payload(segment):
    return {
        "schema_version": "question-conversation-v1",
        "question_id_sha256": "1" * 64,
        "units": [
            {
                "summary": "Use idempotency_key with retry count 3.",
                "source_segment_sha256": [segment.content_sha256],
                "supporting_excerpts": ["idempotency_key"],
            }
        ],
        "unresolved_topics": [],
        "source_message_count": 1,
    }


def make_intent(*preserve):
    return CompressionIntent(
        schema_version="compression-intent-v1",
        consumer_operation="followup",
        phase="interview",
        source_focus=None,
        current_focus="retry safety",
        preserve=preserve,
        authority="non_authoritative",
        prohibited_authority_upgrades=[
            "candidate_exact_quote",
            "authoritative_scoring_evidence",
            "new_fact",
            "identity_inference",
        ],
    )


def validate(payload, segment, *, policy=None, intent=None):
    return validate_compression_artifact(
        policy=policy or make_policy(),
        payload=payload,
        source_segments=[segment],
        estimator=CharacterEstimator(),
        model="test-model",
        expected_question_id_sha256="1" * 64,
        intent=intent,
    )


def test_validated_artifact_is_grounded_bounded_and_input_immutable():
    segment = make_segment()
    payload = make_payload(segment)
    original = deepcopy(payload)

    result = validate(payload, segment)

    assert result.payload.schema_version == "question-conversation-v1"
    assert result.stats.source_segment_count == 1
    assert result.stats.output_unit_count == 1
    assert result.stats.supporting_excerpt_count == 1
    assert result.stats.estimated_output_tokens > 0
    assert payload == original


def test_unknown_anchor_and_non_contiguous_excerpt_fail_without_leaking_content():
    segment = make_segment()
    payload = make_payload(segment)
    payload["units"][0]["source_segment_sha256"] = ["f" * 64]

    with pytest.raises(ContextArtifactValidationFailed) as captured:
        validate(payload, segment)
    assert "idempotency" not in str(captured.value)

    payload = make_payload(segment)
    payload["units"][0]["supporting_excerpts"] = ["not in source"]
    with pytest.raises(ContextArtifactValidationFailed, match="supporting excerpt"):
        validate(payload, segment)


@pytest.mark.parametrize(
    "summary",
    [
        "Use idempotency_key with retry count 99.",
        "Use invented_identifier with retry count 3.",
    ],
)
def test_summary_cannot_introduce_new_numbers_or_identifiers(summary):
    segment = make_segment()
    payload = make_payload(segment)
    payload["units"][0]["summary"] = summary

    with pytest.raises(ContextArtifactValidationFailed, match="grounding"):
        validate(payload, segment)


def test_intent_number_preservation_rejects_omission_from_all_output_text():
    segment = make_segment()
    payload = make_payload(segment)
    payload["units"][0]["summary"] = "Use idempotency_key"
    payload["units"][0]["supporting_excerpts"] = [
        "idempotency_key with retry count 3"
    ]

    with pytest.raises(ContextArtifactValidationFailed, match="required number"):
        validate(payload, segment, intent=make_intent("numbers"))


def test_intent_identifier_preservation_rejects_omission_from_all_output_text():
    segment = make_segment()
    payload = make_payload(segment)
    payload["units"][0]["summary"] = "retry count 3"
    payload["units"][0]["supporting_excerpts"] = [
        "idempotency_key with retry count 3"
    ]

    with pytest.raises(ContextArtifactValidationFailed, match="required identifier"):
        validate(payload, segment, intent=make_intent("identifiers"))


def test_intent_aware_unit_requires_supporting_excerpt_and_exact_source_summary():
    segment = make_segment()
    payload = make_payload(segment)
    payload["units"][0]["supporting_excerpts"] = []

    with pytest.raises(ContextArtifactValidationFailed, match="supporting excerpt"):
        validate(payload, segment, intent=make_intent("candidate_claims"))

    payload = make_payload(segment)
    payload["units"][0]["summary"] = (
        "This guarantees perfect delivery under every failure mode."
    )
    with pytest.raises(ContextArtifactValidationFailed, match="exact source excerpt"):
        validate(payload, segment, intent=make_intent("candidate_claims"))


def test_intent_aware_summary_accepts_exact_case_sensitive_source_excerpt():
    segment = make_segment()
    payload = make_payload(segment)
    payload["units"][0]["summary"] = "idempotency_key with retry count 3"

    result = validate(
        payload,
        segment,
        intent=make_intent("numbers", "identifiers"),
    )

    assert result.payload.units[0].summary == "idempotency_key with retry count 3"


def test_summary_and_supporting_excerpt_may_use_different_cited_sources():
    summary_source = make_segment("Candidate chose Redis for cache consistency.")
    excerpt_content = "The observed retry count was 3."
    excerpt_source = CompressionSourceSegment(
        segment_index=1,
        segment_type="conversation_message",
        content=excerpt_content,
        content_sha256=sha256(excerpt_content.encode("utf-8")).hexdigest(),
    )
    payload = make_payload(summary_source)
    payload["units"][0] = {
        "summary": "Candidate chose Redis for cache consistency.",
        "source_segment_sha256": [
            summary_source.content_sha256,
            excerpt_source.content_sha256,
        ],
        "supporting_excerpts": ["retry count was 3"],
    }
    payload["source_message_count"] = 2

    result = validate_compression_artifact(
        policy=make_policy(),
        payload=payload,
        source_segments=[summary_source, excerpt_source],
        estimator=CharacterEstimator(),
        model="test-model",
        expected_question_id_sha256="1" * 64,
        intent=make_intent("candidate_claims"),
    )

    assert result.payload.units[0].summary == payload["units"][0]["summary"]


def test_unit_excerpt_and_total_output_limits_fail_closed():
    segment = make_segment()
    payload = make_payload(segment)
    payload["units"] = payload["units"] * 2
    with pytest.raises(ContextArtifactValidationFailed, match="unit limit"):
        validate(payload, segment, policy=make_policy(max_output_units=1))

    payload = make_payload(segment)
    with pytest.raises(ContextArtifactValidationFailed, match="excerpt budget"):
        validate(
            payload,
            segment,
            policy=make_policy(max_supporting_excerpt_tokens=2),
        )

    with pytest.raises(ContextArtifactValidationFailed, match="output budget"):
        validate(
            make_payload(segment),
            segment,
            policy=make_policy(target_output_tokens=5),
        )


def test_provider_cannot_fabricate_payload_identity_or_source_count():
    segment = make_segment()
    payload = make_payload(segment)
    payload["question_id_sha256"] = "9" * 64

    with pytest.raises(ContextArtifactValidationFailed, match="question identity"):
        validate(payload, segment)

    payload = make_payload(segment)
    payload["source_message_count"] = 2
    with pytest.raises(ContextArtifactValidationFailed, match="source count"):
        validate(payload, segment)


def test_summary_grounding_is_scoped_to_the_units_own_anchors():
    first = make_segment("Use idempotency_key with retry count 3.")
    second_content = "A separate source mentions foreign_identifier."
    second = CompressionSourceSegment(
        segment_index=1,
        segment_type="conversation_message",
        content=second_content,
        content_sha256=sha256(second_content.encode("utf-8")).hexdigest(),
    )
    payload = make_payload(first)
    payload["units"][0]["summary"] = "Use foreign_identifier with retry count 3."
    payload["source_message_count"] = 2

    with pytest.raises(ContextArtifactValidationFailed, match="grounding"):
        validate_compression_artifact(
            policy=make_policy(),
            payload=payload,
            source_segments=[first, second],
            estimator=CharacterEstimator(),
            model="test-model",
            expected_question_id_sha256="1" * 64,
        )


def test_programmable_validation_does_not_claim_free_language_semantic_authority():
    segment = make_segment()
    payload = make_payload(segment)
    payload["units"][0]["summary"] = (
        "This guarantees perfect delivery under every failure mode."
    )

    result = validate(payload, segment)

    assert result.payload.units[0].summary == payload["units"][0]["summary"]
    assert not hasattr(result, "semantic_authority")
    assert not hasattr(result.payload.units[0], "semantic_authority")


def test_shared_source_keyword_cannot_ground_a_fabricated_conclusion():
    segment = make_segment(
        "Candidate chose Redis because it reduced latency by 20 percent."
    )
    payload = make_payload(segment)
    payload["units"][0]["summary"] = (
        "Redis guarantees ideal behavior in every failure mode."
    )
    payload["units"][0]["supporting_excerpts"] = ["chose Redis"]

    with pytest.raises(ContextArtifactValidationFailed, match="exact source excerpt"):
        validate(payload, segment, intent=make_intent("candidate_claims"))


def test_question_memory_validation_preserves_non_authoritative_semantic_boundary():
    content = "Candidate chose Redis because it reduced latency by 20 percent."
    segment = make_segment(content)
    policy = make_policy(
        artifact_type="question_memory",
        policy_version="question-memory-v1",
        prompt_contract_version="question-memory-prompt-v1",
        output_schema_version="question-memory-v1",
        target_output_tokens=5_000,
    )
    payload = {
        "schema_version": "question-memory-v1",
        "authority": "non_authoritative",
        "session_scope_sha256": "2" * 64,
        "question_id_sha256": "1" * 64,
        "question_focus_sha256": "3" * 64,
        "source_manifest_sha256": "4" * 64,
        "source_message_count": 1,
        "claims": [
            {
                "claim_type": "result",
                "summary": (
                    "This choice guarantees ideal behavior in every failure mode."
                ),
                "polarity": "positive",
                "source_segment_sha256": [segment.content_sha256],
                "supporting_excerpts": ["chose Redis"],
                "confidence": "low",
            }
        ],
        "unresolved_topics": [],
    }

    result = validate_compression_artifact(
        policy=policy,
        payload=payload,
        source_segments=[segment],
        estimator=CharacterEstimator(),
        model="test-model",
        expected_session_scope_sha256="2" * 64,
        expected_question_id_sha256="1" * 64,
        expected_question_focus_sha256="3" * 64,
        expected_source_manifest_sha256="4" * 64,
    )

    assert result.payload.authority == "non_authoritative"
    assert not hasattr(result.payload, "scoring_evidence")


@pytest.mark.parametrize(
    ("field", "expected", "message"),
    [
        ("session_scope_sha256", "9" * 64, "session scope"),
        ("question_id_sha256", "9" * 64, "question identity"),
        ("question_focus_sha256", "9" * 64, "question focus"),
        ("source_manifest_sha256", "9" * 64, "source manifest"),
    ],
)
def test_question_memory_identity_digests_fail_closed(field, expected, message):
    content = "Candidate described a cache tradeoff."
    segment = make_segment(content)
    policy = make_policy(
        artifact_type="question_memory",
        output_schema_version="question-memory-v1",
        target_output_tokens=5_000,
    )
    payload = {
        "schema_version": "question-memory-v1",
        "authority": "non_authoritative",
        "session_scope_sha256": "2" * 64,
        "question_id_sha256": "1" * 64,
        "question_focus_sha256": "3" * 64,
        "source_manifest_sha256": "4" * 64,
        "source_message_count": 1,
        "claims": [],
        "unresolved_topics": [],
    }
    kwargs = {
        "expected_session_scope_sha256": "2" * 64,
        "expected_question_id_sha256": "1" * 64,
        "expected_question_focus_sha256": "3" * 64,
        "expected_source_manifest_sha256": "4" * 64,
    }
    kwargs[f"expected_{field}"] = expected

    with pytest.raises(ContextArtifactValidationFailed, match=message):
        validate_compression_artifact(
            policy=policy,
            payload=payload,
            source_segments=[segment],
            estimator=CharacterEstimator(),
            model="test-model",
            **kwargs,
        )
