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


def validate(payload, segment, *, policy=None):
    return validate_compression_artifact(
        policy=policy or make_policy(),
        payload=payload,
        source_segments=[segment],
        estimator=CharacterEstimator(),
        model="test-model",
        expected_question_id_sha256="1" * 64,
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
