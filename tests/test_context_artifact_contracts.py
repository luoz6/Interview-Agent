from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timezone
from hashlib import sha256
import json

import pytest

from app.services.context_artifacts import (
    ContextArtifactClaim,
    ContextArtifactCleanupPolicy,
    ContextArtifactIdentity,
    ContextArtifactIdentityMaterial,
    ContextArtifactRecord,
    ContextArtifactRef,
    ContextCompressorConfig,
    canonical_compressor_settings_payload,
    canonical_identity_payload,
    compressor_settings_sha256,
)


def make_material(**changes):
    material = ContextArtifactIdentityMaterial(
        artifact_type="question_conversation",
        privacy_scope_sha256="1" * 64,
        source_sha256="2" * 64,
        source_manifest_sha256="3" * 64,
        semantic_focus_sha256="4" * 64,
        compression_policy_version="conversation-v1",
        prompt_contract_version="compressor-prompt-v1",
        output_schema_version="question-conversation-v1",
        compressor_provider="openai-compatible",
        compressor_model="gpt-4o",
        compressor_settings_sha256="5" * 64,
        target_output_tokens=256,
    )
    return replace(material, **changes)


def test_artifact_key_is_derived_only_from_canonical_identity_material():
    material = make_material()
    identity = ContextArtifactIdentity.from_material(material)
    canonical = canonical_identity_payload(material)

    assert "artifact_key" not in json.loads(canonical)
    assert identity.artifact_key == sha256(canonical.encode("utf-8")).hexdigest()
    assert ContextArtifactIdentity.from_material(material) == identity


def test_every_immutable_identity_change_produces_a_different_key():
    original = ContextArtifactIdentity.from_material(make_material())

    for field_name, changed_value in (
        ("artifact_type", "evidence_compression"),
        ("privacy_scope_sha256", "a" * 64),
        ("source_sha256", "b" * 64),
        ("source_manifest_sha256", "e" * 64),
        ("semantic_focus_sha256", "c" * 64),
        ("compression_policy_version", "conversation-v2"),
        ("prompt_contract_version", "compressor-prompt-v2"),
        ("output_schema_version", "question-conversation-v2"),
        ("compressor_provider", "other-compatible"),
        ("compressor_model", "deepseek-chat"),
        ("compressor_settings_sha256", "d" * 64),
        ("target_output_tokens", 128),
    ):
        changed = ContextArtifactIdentity.from_material(
            make_material(**{field_name: changed_value})
        )
        assert changed.artifact_key != original.artifact_key


def test_identity_rejects_non_sha_digests_and_non_positive_budget():
    with pytest.raises(ValueError, match="privacy_scope_sha256"):
        make_material(privacy_scope_sha256="not-a-digest")
    with pytest.raises(ValueError, match="target_output_tokens"):
        make_material(target_output_tokens=0)


def test_claim_record_and_ref_are_distinct_immutable_contracts():
    identity = ContextArtifactIdentity.from_material(make_material())
    claim = ContextArtifactClaim(
        artifact_id="artifact-1",
        artifact_key=identity.artifact_key,
        status="running",
        claim_token="claim-1",
        fencing_version=1,
        claim_owner="worker-1",
        output_sha256=None,
        payload=None,
    )
    record = ContextArtifactRecord(
        artifact_id="artifact-1",
        identity=identity,
        status="failed",
        output_sha256=None,
        payload=None,
        last_error_code="provider_timeout",
        completed_at=None,
    )
    ref = ContextArtifactRef(
        artifact_ref="context-artifact-ref:ref-1",
        artifact_sha256="6" * 64,
        artifact_type="question_conversation",
        compression_policy_version="conversation-v1",
    )

    with pytest.raises(FrozenInstanceError):
        claim.status = "completed"
    assert claim.artifact_key == identity.artifact_key
    assert not hasattr(claim, "identity")
    with pytest.raises(FrozenInstanceError):
        record.status = "completed"
    with pytest.raises(Exception):
        ref.artifact_sha256 = "7" * 64
    with pytest.raises(ValueError, match="claim status"):
        ContextArtifactClaim(
            artifact_id="artifact-1",
            artifact_key=identity.artifact_key,
            status="failed",
            claim_token=None,
            fencing_version=1,
            claim_owner=None,
            output_sha256=None,
            payload=None,
        )


def test_compressor_settings_identity_is_non_secret_and_behavior_bound():
    config = ContextCompressorConfig(
        provider="openai-compatible",
        model="gpt-4o",
        base_url_identity="https://api.example.com/v1",
        temperature=0.1,
        request_timeout_seconds=30.0,
        timeout_policy_version="timeout-v1",
        max_retries=2,
        structured_output_mode="json_schema",
        tokenizer_family="cl100k_base",
    )
    canonical = canonical_compressor_settings_payload(config)

    assert "api_key" not in canonical
    assert "authorization" not in canonical.lower()
    assert "request_timeout_seconds" in canonical
    assert compressor_settings_sha256(replace(config, request_timeout_seconds=31.0)) != (
        compressor_settings_sha256(config)
    )
    assert compressor_settings_sha256(replace(config, max_retries=3)) != (
        compressor_settings_sha256(config)
    )


def test_compressor_base_url_identity_is_normalized_and_rejects_credentials():
    config = ContextCompressorConfig(
        provider="openai-compatible",
        model="gpt-4o",
        base_url_identity="HTTPS://API.EXAMPLE.COM:443/v1/",
        temperature=0,
        request_timeout_seconds=30,
        timeout_policy_version="timeout-v1",
        max_retries=0,
        structured_output_mode="json_schema",
        tokenizer_family=None,
    )

    assert config.base_url_identity == "https://api.example.com/v1"
    with pytest.raises(ValueError, match="credentials"):
        replace(config, base_url_identity="https://user:secret@example.com/v1")


def test_cleanup_policy_requires_aware_cutoffs_and_positive_batch_size():
    now = datetime.now(timezone.utc)
    ContextArtifactCleanupPolicy(
        completed_before=now,
        failed_before=now,
        prep_ref_expires_before=now,
        batch_size=10,
    )

    with pytest.raises(ValueError, match="timezone-aware"):
        ContextArtifactCleanupPolicy(
            completed_before=now.replace(tzinfo=None),
            failed_before=now,
            prep_ref_expires_before=now,
            batch_size=10,
        )
    with pytest.raises(ValueError, match="batch_size"):
        ContextArtifactCleanupPolicy(
            completed_before=now,
            failed_before=now,
            prep_ref_expires_before=now,
            batch_size=0,
        )
