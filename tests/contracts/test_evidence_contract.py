from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import math

import pytest
from pydantic import ValidationError

from contracts.evidence import (
    AtomicEvidenceWriter,
    CapacityEvidencePayload,
    EvidenceArtifact,
    EvidenceBundle,
    EvidenceEnvelope,
    EvidenceIssuer,
    EvidenceRegistry,
    EvidenceVerifier,
    HmacReceiptSigner,
    InputArtifact,
    PrivacyMetadata,
    PromotionDecision,
    VerificationStatus,
)
from contracts.evidence.canonical import CanonicalizationError, canonical_json
from contracts.evidence.digest import canonical_sha256
from contracts.evidence.digest import sha256_bytes
from contracts.evidence.privacy import PrivacyViolation, assert_privacy_safe
from contracts.policies import CapacityEvidencePolicy
from contracts.policies import EvidencePolicyResult


REVISION = "abcdef1"
SCOPE = "capacity.test"
NOW = datetime(2026, 8, 10, 13, 0, 0, tzinfo=timezone.utc)
SIGNER = HmacReceiptSigner(key_id="test-key-v1", secret=b"k" * 32)


def _payload() -> CapacityEvidencePayload:
    return CapacityEvidencePayload(
        schema_version="capacity-evidence-v1",
        sample_count=24,
        server_available_capacity=100,
        configured_process_budget=45,
        allowed_process_budget=75,
        observed_application_peak=20,
        expected_application_peak=20,
        application_peak_tolerance=1,
        observed_advisory_locks=4,
        expected_advisory_locks=4,
        observed_checkpointer_peak=2,
        checkpointer_budgeted_max=3,
        headroom_percent=40.0,
        simultaneous_domains_verified=True,
        schema_ready=True,
        load_error_count=0,
        privacy_violation_count=0,
        domains={
            "business": {
                "max_size": 12,
                "peak_leased": 12,
                "acquire_timeout_count": 0,
                "discard_count": 0,
                "p95_wait_ms": 1.0,
            }
        },
        synthetic=False,
    )


def _bundle() -> EvidenceBundle:
    manifest = [
        InputArtifact(
            path="artifacts/input.json",
            sha256="a" * 64,
            receipt_sha256="d" * 64,
            size_bytes=128,
            media_type="application/json",
        )
    ]
    envelope = EvidenceEnvelope(
        schema_version="evidence-envelope-v1",
        producer="tests.capacity",
        tool_version="1.0.0",
        revision=REVISION,
        scope=SCOPE,
        input_manifest=manifest,
        input_digest=canonical_sha256(
            [item.model_dump(mode="json") for item in manifest]
        ),
        generated_at="2026-08-10T12:00:00Z",
        expires_at="2026-08-11T12:00:00Z",
        privacy=PrivacyMetadata(
            classification="internal",
            contains_personal_data=False,
            redaction_applied=False,
            forbidden_fields_checked=True,
        ),
        receipt_id="b" * 64,
    )
    artifact = EvidenceArtifact(
        envelope=envelope,
        payload_type="capacity-evidence",
        payload=_payload().model_dump(mode="json"),
        verification_status=VerificationStatus.PASS,
        promotion_decision=PromotionDecision.READY_FOR_REVIEW,
        gate_codes=[],
    )
    receipt = SIGNER.issue(
        artifact=artifact,
        issued_at="2026-08-10T12:00:01Z",
    )
    return EvidenceBundle(artifact=artifact, receipt=receipt)


def _value() -> dict:
    return _bundle().model_dump(mode="json")


def _verifier() -> EvidenceVerifier:
    return EvidenceVerifier(
        registry=EvidenceRegistry.default(),
        receipt_signer=SIGNER,
    )


def test_valid_bundle_verifies_and_returns_domain_payload():
    verified = _verifier().verify(
        _value(),
        expected_revision=REVISION,
        expected_scope=SCOPE,
        now=NOW,
    )

    assert isinstance(verified.payload, CapacityEvidencePayload)
    assert verified.payload.sample_count == 24


@pytest.mark.parametrize(
    "field",
    [
        "schema_version",
        "producer",
        "tool_version",
        "revision",
        "scope",
        "input_manifest",
        "input_digest",
        "generated_at",
        "privacy",
        "receipt_id",
    ],
)
def test_missing_required_envelope_field_is_rejected(field):
    value = _value()
    del value["artifact"]["envelope"][field]

    with pytest.raises(ValueError):
        _verifier().verify(
            value,
            expected_revision=REVISION,
            expected_scope=SCOPE,
            now=NOW,
        )


@pytest.mark.parametrize(
    "target",
    ["bundle", "artifact", "envelope", "payload", "receipt"],
)
def test_unknown_fields_are_rejected_at_every_contract_boundary(target):
    value = _value()
    if target == "bundle":
        value["unknown"] = True
    elif target == "artifact":
        value["artifact"]["unknown"] = True
    elif target == "envelope":
        value["artifact"]["envelope"]["unknown"] = True
    elif target == "payload":
        value["artifact"]["payload"]["unknown"] = True
    else:
        value["receipt"]["unknown"] = True

    with pytest.raises(ValueError):
        _verifier().verify(
            value,
            expected_revision=REVISION,
            expected_scope=SCOPE,
            now=NOW,
        )


@pytest.mark.parametrize("replacement", ["false", 0, 1])
def test_string_and_integer_booleans_are_rejected(replacement):
    value = _value()
    value["artifact"]["payload"]["synthetic"] = replacement

    with pytest.raises(ValueError):
        _verifier().verify(
            value,
            expected_revision=REVISION,
            expected_scope=SCOPE,
            now=NOW,
        )


@pytest.mark.parametrize("replacement", ["20", 20.0, True])
def test_non_integer_sample_counts_are_rejected(replacement):
    value = _value()
    value["artifact"]["payload"]["sample_count"] = replacement

    with pytest.raises(ValueError):
        _verifier().verify(
            value,
            expected_revision=REVISION,
            expected_scope=SCOPE,
            now=NOW,
        )


@pytest.mark.parametrize("replacement", [math.nan, math.inf, -math.inf])
def test_non_finite_numbers_are_rejected(replacement):
    value = _value()
    value["artifact"]["payload"]["headroom_percent"] = replacement

    with pytest.raises((ValueError, CanonicalizationError)):
        _verifier().verify(
            value,
            expected_revision=REVISION,
            expected_scope=SCOPE,
            now=NOW,
        )


@pytest.mark.parametrize(
    ("field", "replacement", "message"),
    [
        ("revision", "deadbee", "revision mismatch"),
        ("scope", "other.scope", "scope mismatch"),
        ("input_digest", "0" * 64, "manifest digest mismatch"),
    ],
)
def test_revision_scope_and_manifest_digest_are_bound(field, replacement, message):
    value = _value()
    value["artifact"]["envelope"][field] = replacement

    with pytest.raises(ValueError, match=message):
        _verifier().verify(
            value,
            expected_revision=REVISION,
            expected_scope=SCOPE,
            now=NOW,
        )


def test_manifest_file_set_mutation_is_rejected():
    value = _value()
    value["artifact"]["envelope"]["input_manifest"].append(
        {
            "path": "artifacts/injected.json",
            "sha256": "c" * 64,
            "receipt_sha256": "e" * 64,
            "size_bytes": 1,
            "media_type": "application/json",
        }
    )

    with pytest.raises(ValueError, match="manifest digest mismatch"):
        _verifier().verify(
            value,
            expected_revision=REVISION,
            expected_scope=SCOPE,
            now=NOW,
        )


@pytest.mark.parametrize("path", ["../secret", "/absolute", "C:/absolute"])
def test_manifest_rejects_path_traversal_and_absolute_paths(path):
    value = _value()
    value["artifact"]["envelope"]["input_manifest"][0]["path"] = path

    with pytest.raises(ValueError):
        _verifier().verify(
            value,
            expected_revision=REVISION,
            expected_scope=SCOPE,
            now=NOW,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("password", "secret"),
        ("dsn", "postgresql://user:secret@db/interview"),
        ("private_key", "-----BEGIN PRIVATE KEY-----"),
    ],
)
def test_sensitive_payload_fields_are_rejected(field, value):
    with pytest.raises(PrivacyViolation):
        assert_privacy_safe({field: value})


def test_receipt_detects_artifact_mutation():
    value = _value()
    value["artifact"]["payload"]["observed_application_peak"] = 21

    with pytest.raises(ValueError, match="receipt evidence digest mismatch"):
        _verifier().verify(
            value,
            expected_revision=REVISION,
            expected_scope=SCOPE,
            now=NOW,
        )


@pytest.mark.parametrize(
    ("changes", "expected_gate"),
    [
        ({"sample_count": 23}, "CAPACITY_SAMPLE_COUNT_MISMATCH"),
        ({"configured_process_budget": 76}, "CAPACITY_PROCESS_BUDGET_EXCEEDED"),
        ({"headroom_percent": 99.0}, "CAPACITY_HEADROOM_MISMATCH"),
        ({"schema_ready": False}, "CAPACITY_SCHEMA_NOT_READY"),
        (
            {"simultaneous_domains_verified": False},
            "CAPACITY_SIMULTANEOUS_OBSERVATION_MISSING",
        ),
        ({"load_error_count": 1}, "CAPACITY_LOAD_ERRORS_PRESENT"),
        ({"privacy_violation_count": 1}, "CAPACITY_PRIVACY_VIOLATION"),
        ({"observed_application_peak": 19}, "CAPACITY_APPLICATION_PEAK_TOO_LOW"),
        ({"observed_advisory_locks": 3}, "CAPACITY_ADVISORY_LOCK_OBSERVATION_LOW"),
        ({"observed_checkpointer_peak": 4}, "CAPACITY_CHECKPOINTER_BUDGET_EXCEEDED"),
    ],
)
def test_capacity_policy_derives_failures_from_decisive_fields(changes, expected_gate):
    payload = _payload().model_copy(update=changes)
    policy = CapacityEvidencePolicy(
        minimum_samples=1,
        minimum_headroom_percent=0.0,
    )

    result = policy.evaluate(payload, production_scope=False)

    assert result.verification_status is VerificationStatus.BLOCKED
    assert expected_gate in result.gate_codes


def test_receipt_detects_signature_mutation():
    value = _value()
    value["receipt"]["signature"] = "f" * 64

    with pytest.raises(ValueError, match="signature verification failed"):
        _verifier().verify(
            value,
            expected_revision=REVISION,
            expected_scope=SCOPE,
            now=NOW,
        )


def test_blocked_and_not_run_status_cannot_claim_ready():
    with pytest.raises(ValidationError, match="can only have HOLD"):
        EvidenceArtifact(
            envelope=_bundle().artifact.envelope,
            payload_type="capacity-evidence",
            payload=_payload().model_dump(mode="json"),
            verification_status=VerificationStatus.NOT_RUN,
            promotion_decision=PromotionDecision.READY,
            gate_codes=["CAPACITY_NOT_RUN"],
        )


def test_zero_samples_cannot_construct_capacity_evidence():
    value = _payload().model_dump(mode="json")
    value["sample_count"] = 0

    with pytest.raises(ValidationError):
        CapacityEvidencePayload.model_validate(value)


def test_synthetic_capacity_cannot_be_promoted_as_production():
    synthetic = _payload().model_copy(update={"synthetic": True})
    policy = CapacityEvidencePolicy(
        minimum_samples=10,
        minimum_headroom_percent=20.0,
    )

    result = policy.evaluate(synthetic, production_scope=True)

    assert result.verification_status is VerificationStatus.BLOCKED
    assert result.promotion_decision is PromotionDecision.HOLD
    assert "SYNTHETIC_RESULT_NOT_PRODUCTION" in result.gate_codes


def test_atomic_writer_persists_canonical_lf_json_and_returns_digest(tmp_path):
    target = tmp_path / "evidence.json"

    digest = AtomicEvidenceWriter().write(target, _bundle())

    persisted = target.read_bytes()
    assert persisted.endswith(b"\n")
    assert b"\r" not in persisted
    assert sha256_bytes(persisted) == digest
    assert target.parent.joinpath("unused").exists() is False
    assert canonical_json(_bundle()).encode("utf-8") == persisted[:-1]


def test_evidence_issuer_builds_signed_self_verified_bundle():
    issuer = EvidenceIssuer(signer=SIGNER, clock=lambda: NOW)
    policy_result = EvidencePolicyResult(
        verification_status=VerificationStatus.PASS,
        promotion_decision=PromotionDecision.READY_FOR_REVIEW,
        gate_codes=(),
    )

    bundle = issuer.issue(
        payload_type="capacity-evidence",
        payload=_payload(),
        policy_result=policy_result,
        producer="tests.capacity",
        tool_version="1.0.0",
        revision=REVISION,
        scope=SCOPE,
    )

    verified = _verifier().verify(
        bundle.model_dump(mode="json"),
        expected_revision=REVISION,
        expected_scope=SCOPE,
        now=NOW,
    )
    assert verified.payload == _payload()
    assert bundle.receipt.signature is not None
    assert bundle.artifact.envelope.input_digest == canonical_sha256([])


def test_evidence_issuer_is_deterministic_for_same_timestamp_and_inputs():
    issuer = EvidenceIssuer(signer=SIGNER, clock=lambda: NOW)
    policy_result = EvidencePolicyResult(
        verification_status=VerificationStatus.BLOCKED,
        promotion_decision=PromotionDecision.HOLD,
        gate_codes=("CAPACITY_HEADROOM_INSUFFICIENT",),
    )
    arguments = {
        "payload_type": "capacity-evidence",
        "payload": _payload(),
        "policy_result": policy_result,
        "producer": "tests.capacity",
        "tool_version": "1.0.0",
        "revision": REVISION,
        "scope": SCOPE,
    }

    first = issuer.issue(**arguments)
    second = issuer.issue(**arguments)

    assert first == second


def test_atomic_writer_invokes_full_post_write_verifier(tmp_path):
    verified_values = []
    verifier = _verifier()

    def verify(value):
        verified_values.append(
            verifier.verify(
                value,
                expected_revision=REVISION,
                expected_scope=SCOPE,
                now=NOW,
            )
        )

    target = tmp_path / "evidence.json"
    AtomicEvidenceWriter(post_write_verifier=verify).write(target, _bundle())

    assert len(verified_values) == 1
    assert verified_values[0].payload == _payload()
