from dataclasses import FrozenInstanceError, asdict, fields, replace
from hashlib import sha256
import inspect
import json

import pytest
from pydantic import ValidationError

from app.domain.context.artifacts import (
    CompressionSourceSegment,
    ContextArtifactIdentity,
    ContextArtifactIdentityMaterial,
    ContextCompressionPolicy,
    canonical_identity_payload,
)
from app.services.context_compression_intent import CompressionIntent


def request_api():
    from app.services.context_compression_request import (
        ResolvedCompressionRequest,
        bind_resolved_target_to_identity,
    )

    return ResolvedCompressionRequest, bind_resolved_target_to_identity


def target_policy(**changes):
    from app.services.context_budget import DynamicCompressionTargetPolicy

    values = {
        "floor_tokens": 256,
        "source_ratio_basis_points": 2_500,
        "allowed_target_tokens": (256, 512, 1_024, 1_536, 2_000),
    }
    values.update(changes)
    return DynamicCompressionTargetPolicy(**values)


def compression_policy(**changes):
    values = {
        "artifact_type": "question_conversation",
        "policy_version": "conversation-v1",
        "prompt_contract_version": "compressor-prompt-v1",
        "output_schema_version": "question-conversation-v1",
        "compressor_operation": "context_compressor.question_conversation",
        "compressor_input_cap_tokens": 16_000,
        "target_output_tokens": 2_000,
        "max_output_units": 16,
        "max_supporting_excerpt_tokens": 128,
    }
    values.update(changes)
    return ContextCompressionPolicy(**values)


def source_segment(content="historical answer"):
    return CompressionSourceSegment(
        segment_index=0,
        segment_type="conversation_message",
        content=content,
        content_sha256=sha256(content.encode("utf-8")).hexdigest(),
    )


def compression_intent():
    return CompressionIntent(
        schema_version="compression-intent-v1",
        consumer_operation="followup",
        phase="interview",
        source_focus=None,
        current_focus="cache consistency",
        preserve=("candidate_claims", "numbers"),
        authority="non_authoritative",
        prohibited_authority_upgrades=("new_fact",),
    )


def resolved_request(**changes):
    ResolvedCompressionRequest, _identity_helper = request_api()
    values = {
        "policy": compression_policy(),
        "intent": None,
        "source_segments": (source_segment(),),
        "resolved_target_output_tokens": 2_000,
        "target_policy": None,
    }
    values.update(changes)
    return ResolvedCompressionRequest(**values)


def identity_material(**changes):
    values = {
        "artifact_type": "question_conversation",
        "privacy_scope_sha256": "1" * 64,
        "source_sha256": "2" * 64,
        "source_manifest_sha256": "3" * 64,
        "semantic_focus_sha256": "4" * 64,
        "compression_policy_version": "conversation-v1",
        "prompt_contract_version": "compressor-prompt-v1",
        "output_schema_version": "question-conversation-v1",
        "compressor_provider": "openai-compatible",
        "compressor_model": "gpt-4o",
        "compressor_settings_sha256": "5" * 64,
        "target_output_tokens": 2_000,
    }
    values.update(changes)
    return ContextArtifactIdentityMaterial(**values)


def test_resolved_compression_request_is_frozen_and_keeps_tuple_sources():
    request = resolved_request()

    assert isinstance(request.source_segments, tuple)
    assert request.source_segments[0].model_dump() == source_segment().model_dump()
    with pytest.raises(FrozenInstanceError):
        request.resolved_target_output_tokens = 1_024


def test_resolved_compression_request_owns_an_immutable_source_snapshot():
    original = source_segment()
    request = resolved_request(source_segments=(original,))
    snapshot = request.source_segments[0]

    assert snapshot is not original
    assert snapshot.model_dump() == original.model_dump()

    changed_content = "mutated after request construction"
    original.content = changed_content
    original.content_sha256 = sha256(changed_content.encode("utf-8")).hexdigest()

    assert snapshot.content == "historical answer"
    assert snapshot.content_sha256 == sha256(
        b"historical answer"
    ).hexdigest()
    with pytest.raises(ValidationError, match="frozen"):
        snapshot.content = changed_content


def test_resolved_compression_request_revalidates_source_content_digest():
    mutated = source_segment()
    mutated.content = "content changed without its digest"

    with pytest.raises(ValidationError, match="content_sha256"):
        resolved_request(source_segments=(mutated,))


def test_resolved_compression_request_rejects_non_tuple_sources():
    with pytest.raises((TypeError, ValueError), match="source_segments.*tuple"):
        resolved_request(source_segments=[source_segment()])

    with pytest.raises((TypeError, ValueError), match="source_segments"):
        resolved_request(source_segments=())

    with pytest.raises((TypeError, ValueError), match="source_segments"):
        resolved_request(source_segments=(object(),))


def test_resolved_compression_request_accepts_optional_canonical_intent():
    absent = resolved_request(intent=None)
    intent = compression_intent()
    present = resolved_request(intent=intent)

    assert absent.intent is None
    assert present.intent == intent
    with pytest.raises((TypeError, ValueError), match="intent"):
        resolved_request(intent=object())


def test_resolved_compression_request_requires_the_policy_contract_type():
    with pytest.raises((TypeError, ValueError), match="policy"):
        resolved_request(policy=object())

    with pytest.raises((TypeError, ValueError), match="target_policy"):
        resolved_request(target_policy=object())


@pytest.mark.parametrize("target", (0, -1, True, 512.0, "512", 2_001))
def test_resolved_target_must_be_positive_and_within_policy_hard_cap(target):
    with pytest.raises((TypeError, ValueError), match="resolved_target"):
        resolved_request(resolved_target_output_tokens=target)


def test_legacy_fixed_target_request_does_not_require_a_dynamic_target_policy():
    request = resolved_request(
        target_policy=None,
        resolved_target_output_tokens=2_000,
    )

    assert request.resolved_target_output_tokens == (
        request.policy.target_output_tokens
    )


@pytest.mark.parametrize("target", (512, 783))
def test_fixed_request_rejects_targets_below_the_policy_hard_cap(target):
    with pytest.raises(ValueError, match="equal policy hard cap"):
        resolved_request(
            target_policy=None,
            resolved_target_output_tokens=target,
        )


def test_public_request_constructor_cannot_claim_persisted_index_authority():
    request_type, _identity_helper = request_api()

    assert "resolved_target_authority" not in inspect.signature(request_type).parameters
    with pytest.raises(TypeError, match="resolved_target_authority"):
        resolved_request(
            target_policy=None,
            resolved_target_output_tokens=512,
            resolved_target_authority="persisted_index",
        )
    with pytest.raises(TypeError, match="_persisted"):
        resolved_request(
            target_policy=None,
            resolved_target_output_tokens=512,
            _persisted_authority=object(),
        )


def test_private_persisted_factory_recovers_a_removed_target_tier():
    import app.services.context_compression_request as request_module

    factory = (
        request_module._resolved_compression_request_from_persisted_target
    )

    request = factory(
        policy=compression_policy(),
        intent=None,
        source_segments=(source_segment(),),
        resolved_target_output_tokens=512,
        target_policy=None,
    )

    assert request.resolved_target_output_tokens == 512
    assert request.target_policy is None
    assert request.resolved_target_authority == "persisted_index"
    assert "resolved_target_authority" not in {
        field.name for field in fields(request)
    }
    assert "resolved_target_authority" not in asdict(request)
    assert "persisted_index" not in repr(request)
    assert all(
        value is not request_module._PERSISTED_TARGET_AUTHORITY
        for value in vars(request).values()
    )


@pytest.mark.parametrize("target", (0, -1, True, 512.0, "512", 2_001))
def test_private_persisted_factory_enforces_strict_positive_policy_cap(target):
    from app.services.context_compression_request import (
        _resolved_compression_request_from_persisted_target,
    )

    with pytest.raises((TypeError, ValueError), match="resolved_target"):
        _resolved_compression_request_from_persisted_target(
            policy=compression_policy(),
            intent=None,
            source_segments=(source_segment(),),
            target_policy=None,
            resolved_target_output_tokens=target,
        )


def test_persisted_authority_is_read_only_and_excluded_from_request_equality():
    from app.services.context_compression_request import (
        _resolved_compression_request_from_persisted_target,
    )

    ordinary = resolved_request()
    persisted = _resolved_compression_request_from_persisted_target(
        policy=ordinary.policy,
        intent=ordinary.intent,
        source_segments=ordinary.source_segments,
        resolved_target_output_tokens=ordinary.resolved_target_output_tokens,
        target_policy=ordinary.target_policy,
    )

    assert ordinary == persisted
    assert hash(ordinary) == hash(persisted)
    assert ordinary.resolved_target_authority == "policy_resolution"
    assert persisted.resolved_target_authority == "persisted_index"
    with pytest.raises(FrozenInstanceError):
        persisted.resolved_target_authority = "policy_resolution"


@pytest.mark.parametrize(
    ("policy_changes", "target", "message"),
    (
        ({}, 768, "allowed target tier"),
        (
            {
                "floor_tokens": 256,
                "allowed_target_tokens": (128, 256, 512),
            },
            128,
            "floor",
        ),
    ),
)
def test_dynamic_request_target_must_be_an_allowed_tier_at_or_above_floor(
    policy_changes,
    target,
    message,
):
    with pytest.raises(ValueError, match=message):
        resolved_request(
            target_policy=target_policy(**policy_changes),
            resolved_target_output_tokens=target,
        )


@pytest.mark.parametrize("target", (256, 512, 1_024, 1_536, 2_000))
def test_dynamic_request_accepts_every_configured_allowed_target_tier(target):
    request = resolved_request(
        target_policy=target_policy(),
        resolved_target_output_tokens=target,
    )

    assert request.resolved_target_output_tokens == target


def test_identity_helper_uses_only_the_request_canonical_target():
    _request_type, bind_identity = request_api()
    original = identity_material(target_output_tokens=2_000)
    request = resolved_request(
        target_policy=target_policy(),
        resolved_target_output_tokens=512,
    )

    bound = bind_identity(original, request)

    assert original.target_output_tokens == 2_000
    assert bound.target_output_tokens == 512
    assert bound == replace(original, target_output_tokens=512)
    payload = json.loads(canonical_identity_payload(bound))
    assert payload["target_output_tokens"] == 512
    assert "resolved_target_output_tokens" not in payload
    assert "identity_schema_version" not in payload
    assert "identity-v0" not in payload.values()


def test_identity_helper_preserves_legacy_v0_material_at_fixed_hard_cap():
    _request_type, bind_identity = request_api()
    original = identity_material(target_output_tokens=2_000)
    legacy = resolved_request(
        target_policy=None,
        resolved_target_output_tokens=2_000,
    )

    assert bind_identity(original, legacy) == original


def test_resolved_512_and_1024_targets_produce_different_artifact_identities():
    _request_type, bind_identity = request_api()
    original = identity_material(target_output_tokens=2_000)
    requests = [
        resolved_request(
            target_policy=target_policy(),
            resolved_target_output_tokens=target,
        )
        for target in (512, 1_024)
    ]

    identities = [
        ContextArtifactIdentity.from_material(
            bind_identity(original, request)
        )
        for request in requests
    ]

    assert identities[0].material.target_output_tokens == 512
    assert identities[1].material.target_output_tokens == 1_024
    assert identities[0].artifact_key != identities[1].artifact_key


@pytest.mark.parametrize(
    "material",
    (
        identity_material(compression_policy_version="conversation-v2"),
        identity_material(artifact_type="evidence_compression"),
        identity_material(prompt_contract_version="compressor-prompt-v2"),
        identity_material(output_schema_version="question-conversation-v2"),
    ),
)
def test_identity_helper_rejects_policy_version_or_artifact_type_drift(material):
    _request_type, bind_identity = request_api()

    with pytest.raises(ValueError, match="policy|artifact_type"):
        bind_identity(material, resolved_request())


def test_identity_helper_rejects_non_contract_inputs():
    _request_type, bind_identity = request_api()

    with pytest.raises(TypeError, match="identity material"):
        bind_identity(object(), resolved_request())
    with pytest.raises(TypeError, match="resolved compression request"):
        bind_identity(identity_material(), object())
