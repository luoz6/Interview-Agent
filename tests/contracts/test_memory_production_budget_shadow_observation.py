from __future__ import annotations

import base64
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from contracts.evidence import (
    AtomicEvidenceWriter,
    EvidenceIssuer,
    EvidenceRegistry,
    EvidenceVerifier,
    HmacReceiptSigner,
    InputArtifact,
    ProductionBudgetObservationEvidencePayload,
    ProductionShadowChangePreflightEvidencePayload,
)
from contracts.evidence.digest import canonical_sha256
from contracts.evidence.status import PromotionDecision, VerificationStatus
from contracts.policies import ProductionShadowChangePreflightEvidencePolicy
from scripts import memory_production_budget_shadow_observation as observation_runner
from scripts.memory_production_budget_shadow_observation import (
    AggregateInputBlocked,
    BOUNDARY_FIELDS,
    INPUT_SCHEMA_VERSION,
    OUTPUT_SCHEMA_VERSION,
    build_observation_payload,
    sanitize_aggregate_input,
    validate_aggregate_input,
    validate_observation_artifact,
)


FIXTURE = Path(
    "tests/fixtures/memory_production_budget_shadow/pass_candidate.json"
)


def aggregate_input():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_staging_observation_contract_cannot_be_reused_as_production_input():
    staging = {
        "schema_version": "memory-budget-shadow-observation-v1",
        "data_category": "synthetic",
        "provider_calls": 0,
        "production_observation": "NOT_RUN",
    }

    assert staging["data_category"] == "synthetic"
    assert staging["provider_calls"] == 0
    assert staging["production_observation"] == "NOT_RUN"
    with pytest.raises(AggregateInputBlocked) as raised:
        validate_aggregate_input(staging)
    assert "AGGREGATE_INPUT_SCHEMA_INVALID" in raised.value.codes
    assert "AGGREGATE_DATA_CATEGORY_INVALID" in raised.value.codes


def test_valid_aggregate_is_sanitized_to_a_separate_schema():
    source = aggregate_input()
    result = sanitize_aggregate_input(source)

    assert source["schema_version"] == INPUT_SCHEMA_VERSION
    assert result.artifact["schema_version"] == OUTPUT_SCHEMA_VERSION
    assert result.input_field_count == len(source)
    for key, expected in BOUNDARY_FIELDS.items():
        assert result.artifact[key] == expected
    validate_observation_artifact(result.artifact)


@pytest.mark.parametrize(
    ("mutator", "code"),
    [
        (
            lambda value: value.update({"schema_version": "unknown"}),
            "AGGREGATE_INPUT_SCHEMA_INVALID",
        ),
        (
            lambda value: value.update({"data_category": "candidate"}),
            "AGGREGATE_DATA_CATEGORY_INVALID",
        ),
        (
            lambda value: value.update({"requested_phase": "WRITE_SHADOW"}),
            "REQUESTED_PHASE_NOT_BUDGET_ONLY",
        ),
        (
            lambda value: value.update({"approved_revision": "main"}),
            "APPROVED_REVISION_INVALID",
        ),
        (
            lambda value: value.update({"approved_traffic_percent": 2.0}),
            "APPROVED_TRAFFIC_PERCENT_INVALID",
        ),
        (
            lambda value: value.update({"dsn": "postgresql://private"}),
            "AGGREGATE_INPUT_FIELD_SET_INVALID",
        ),
        (
            lambda value: value.update(
                {"requested_phase": "sk-private-example-value"}
            ),
            "AGGREGATE_SENSITIVE_VALUE_DETECTED",
        ),
        (
            lambda value: value["language_sample_counts"].update(
                {"principal-123": 1}
            ),
            "LANGUAGE_BUCKETS_INVALID",
        ),
    ],
)
def test_invalid_or_sensitive_aggregate_input_is_blocked(mutator, code):
    value = aggregate_input()
    mutator(value)

    with pytest.raises(AggregateInputBlocked) as raised:
        validate_aggregate_input(value)

    assert code in raised.value.codes


def test_observation_boundary_cannot_claim_principal_authorization():
    artifact = sanitize_aggregate_input(aggregate_input()).artifact
    changed = deepcopy(artifact)
    changed["principal_write_shadow_production"] = "AUTHORIZED"

    with pytest.raises(AggregateInputBlocked) as raised:
        validate_observation_artifact(changed)

    assert any("BOUNDARY_INVALID" in code for code in raised.value.codes)


def test_zero_observed_traffic_is_structurally_valid_for_continue_decision():
    value = aggregate_input()
    value["observed_traffic_percent_max"] = 0.0

    validate_aggregate_input(value)


def preflight_payload() -> ProductionShadowChangePreflightEvidencePayload:
    return ProductionShadowChangePreflightEvidencePayload(
        schema_version="production-shadow-change-preflight-evidence-v1",
        validated_revision="a" * 40,
        validated_rc_revision="a982b1f",
        validation_revision="ffc58a1",
        approval_request_verified=True,
        readiness_verified=True,
        approval_record_verified=True,
        approval_roles_verified=5,
        record_is_external=True,
        record_hash_match=True,
        revision_match=True,
        deployment_scope_match=True,
        requested_phase="BUDGET_SHADOW_ONLY",
        traffic_percent=1.0,
        window_duration_hours=24,
        configuration_changed=False,
        principal_write_shadow_production="NOT_AUTHORIZED",
        principal_read_shadow_production="NOT_AUTHORIZED",
        long_term_memory_consumption="BLOCKED",
        production_observation="NOT_RUN",
        synthetic=True,
    )


def _manifest_item(path: str, character: str) -> InputArtifact:
    return InputArtifact(
        path=path,
        sha256=character * 64,
        receipt_sha256=character * 64,
        size_bytes=1,
        media_type="application/json",
    )


def test_observation_payload_is_strict_and_remains_hold_for_synthetic_preflight():
    sanitized = sanitize_aggregate_input(aggregate_input())
    payload = build_observation_payload(
        sanitized,
        preflight=preflight_payload(),
    )

    assert isinstance(payload, ProductionBudgetObservationEvidencePayload)
    assert payload.source_preflight_verified is True
    assert payload.synthetic is True


def test_cli_verifies_preflight_and_writes_protected_observation(
    monkeypatch,
    tmp_path,
    capsys,
):
    secret = b"o" * 32
    signer = HmacReceiptSigner(key_id="observation-test", secret=secret)
    preflight = preflight_payload()
    preflight_bundle = EvidenceIssuer(
        signer=signer,
        clock=lambda: datetime.now(timezone.utc),
    ).issue(
        payload_type="production-shadow-change-preflight-evidence",
        payload=preflight,
        policy_result=ProductionShadowChangePreflightEvidencePolicy().evaluate(
            preflight
        ),
        producer="tests.preflight",
        tool_version="2.0.0",
        revision="abcdef1",
        scope="memory.production-shadow.change-preflight",
        input_manifest=(
            _manifest_item("production-shadow-approval-request", "1"),
            _manifest_item("production-budget-readiness-evidence", "2"),
            _manifest_item("external-production-shadow-approval-record", "3"),
        ),
    )
    preflight_path = tmp_path / "preflight.json"
    AtomicEvidenceWriter().write(preflight_path, preflight_bundle)
    aggregate = aggregate_input()
    aggregate_path = tmp_path / "aggregate.json"
    aggregate_path.write_text(json.dumps(aggregate), encoding="utf-8")
    output = tmp_path / "observation.json"
    monkeypatch.setenv("PREFLIGHT_EVIDENCE_REVISION", "abcdef1")
    monkeypatch.setenv("EVIDENCE_REVISION", "abcdef2")
    monkeypatch.setenv("EVIDENCE_HMAC_KEY_ID", "observation-test")
    monkeypatch.setenv(
        "EVIDENCE_HMAC_SECRET_B64",
        base64.b64encode(secret).decode("ascii"),
    )

    assert observation_runner.main(
        [
            "--preflight-evidence",
            str(preflight_path),
            "--aggregate-input",
            str(aggregate_path),
            "--expected-aggregate-sha256",
            canonical_sha256(aggregate),
            "--output",
            str(output),
        ]
    ) == 0

    verified = EvidenceVerifier(
        registry=EvidenceRegistry.default(),
        receipt_signer=signer,
    ).verify(
        json.loads(output.read_text(encoding="utf-8")),
        expected_revision="abcdef2",
        expected_scope="memory.production-budget-shadow.observation",
    )
    assert isinstance(
        verified.payload,
        ProductionBudgetObservationEvidencePayload,
    )
    assert verified.bundle.artifact.verification_status is VerificationStatus.PASS
    assert verified.bundle.artifact.promotion_decision is PromotionDecision.HOLD
    assert len(verified.bundle.artifact.envelope.input_manifest) == 2
    stdout = capsys.readouterr().out
    assert "VERIFICATION_STATUS=PASS" in stdout
    assert "PROMOTION_DECISION=HOLD" in stdout


def test_cli_rejects_aggregate_digest_mismatch_without_output(
    monkeypatch,
    tmp_path,
    capsys,
):
    aggregate_path = tmp_path / "aggregate.json"
    aggregate_path.write_text(json.dumps(aggregate_input()), encoding="utf-8")
    output = tmp_path / "observation.json"
    monkeypatch.setenv("PREFLIGHT_EVIDENCE_REVISION", "abcdef1")
    monkeypatch.setenv("EVIDENCE_REVISION", "abcdef2")
    monkeypatch.setenv("EVIDENCE_HMAC_KEY_ID", "observation-test")
    monkeypatch.setenv(
        "EVIDENCE_HMAC_SECRET_B64",
        base64.b64encode(b"o" * 32).decode("ascii"),
    )

    assert observation_runner.main(
        [
            "--preflight-evidence",
            str(tmp_path / "missing-preflight.json"),
            "--aggregate-input",
            str(aggregate_path),
            "--expected-aggregate-sha256",
            "0" * 64,
            "--output",
            str(output),
        ]
    ) == 1
    assert not output.exists()
    assert "GATE=OBSERVATION_INPUT_UNVERIFIED" in capsys.readouterr().out
