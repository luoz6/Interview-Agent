from __future__ import annotations

import base64
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from contracts.evidence import (
    AtomicEvidenceWriter,
    EvidenceIssuer,
    EvidenceRegistry,
    EvidenceVerifier,
    HmacReceiptSigner,
    ProductionBudgetReadinessEvidencePayload,
    ProductionShadowApprovalRequestPayload,
    ProductionShadowChangePreflightEvidencePayload,
    input_artifact_from_bundle,
)
from contracts.evidence.status import PromotionDecision, VerificationStatus
from contracts.policies import (
    ProductionBudgetReadinessEvidencePolicy,
    ProductionShadowApprovalRequestPolicy,
    ProductionShadowChangePreflightEvidencePolicy,
)
from scripts import memory_production_shadow_change_preflight as preflight
from scripts.memory_production_shadow_change_preflight import (
    ChangePreflightBlocked,
    PASS_LINES,
    build_preflight_evidence,
    canonical_record_sha256,
    evaluate_change_preflight,
    format_blocked_output,
    repository_snapshot,
)


NOW = datetime(2026, 8, 1, 1, 0, tzinfo=timezone.utc)
REVISION = "a" * 40
DEPLOYMENT_DIGEST = "b" * 64


def approval_request() -> ProductionShadowApprovalRequestPayload:
    return ProductionShadowApprovalRequestPayload(
        schema_version="production-shadow-approval-request-v1",
        validated_rc_revision="a982b1f",
        validation_revision="ffc58a1",
        evidence_environment="isolated_staging",
        evidence_profile="B",
        requested_phase="BUDGET_SHADOW_ONLY",
        approval_status="PENDING",
        required_approval_roles=[
            "change_owner",
            "operations",
            "privacy",
            "security",
            "fairness",
        ],
        maximum_traffic_percent=1.0,
        initial_warmup_traffic_percent=0.1,
        minimum_warmup_minutes=30,
        minimum_warmup_followup_samples=20,
        minimum_observation_hours=24,
        minimum_followup_samples=200,
        provider_input_change=False,
        budget_enforcement=False,
        compression_consumption=False,
        principal_write_shadow=False,
        principal_read_shadow=False,
        principal_memory_consumption=False,
        production_migration=False,
        configuration_changed=False,
        production_observation_not_run=True,
        long_term_consumption_blocked=True,
        synthetic=True,
    )


def readiness_payload() -> ProductionBudgetReadinessEvidencePayload:
    request = approval_request()
    return ProductionBudgetReadinessEvidencePayload(
        schema_version="production-budget-readiness-evidence-v1",
        validated_revision=REVISION,
        validated_rc_revision=request.validated_rc_revision,
        validation_revision=request.validation_revision,
        approval_request_verified=True,
        contracts_present=True,
        offline_source_audit=True,
        observation_probe_status="PASS",
        window_probe_action="START_WARM_UP",
        safe_defaults=True,
        consume_rejected=True,
        hard_stop_clear=True,
        pending_example_gate_codes=[
            "APPROVAL_RECORD_NOT_EXTERNAL",
            "APPROVAL_STATUS_NOT_APPROVED",
        ],
        approval_status="PENDING",
        requested_phase="BUDGET_SHADOW_ONLY",
        change_preflight="BLOCKED",
        configuration_changed=False,
        production_observation="NOT_RUN",
        principal_write_shadow_production="NOT_AUTHORIZED",
        principal_read_shadow_production="NOT_AUTHORIZED",
        long_term_memory_consumption="BLOCKED",
        synthetic=True,
    )


def approved_record() -> dict[str, object]:
    start = NOW - timedelta(hours=1)
    end = start + timedelta(hours=24)
    return {
        "schema_version": "memory-production-shadow-approval-record-v1",
        "approval_status": "APPROVED",
        "requested_phase": "BUDGET_SHADOW_ONLY",
        "approved_revision": REVISION,
        "deployment_scope_sha256": DEPLOYMENT_DIGEST,
        "traffic_percent": 1.0,
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "expires_at": end.isoformat(),
        "change_ticket_sha256": "c" * 64,
        "approvals": {
            role: {
                "decision": "APPROVED",
                "approver_ref_sha256": char * 64,
                "decided_at": (start - timedelta(hours=1)).isoformat(),
            }
            for role, char in (
                ("change_owner", "d"),
                ("operations", "e"),
                ("privacy", "f"),
                ("security", "1"),
                ("fairness", "2"),
            )
        },
    }


def repository_state() -> dict[str, object]:
    return {
        "approval_packet_ready": True,
        "readiness_verified": True,
        "safe_defaults": True,
        "consume_rejected": True,
        "production_observation_not_run": True,
        "hard_stop_clear": True,
        "configuration_changed": False,
    }


def record_sha(value: dict[str, object]) -> str:
    return canonical_record_sha256(value)


def evaluate(value=None, **overrides):
    record = value or approved_record()
    options = {
        "record": record,
        "expected_record_sha256": record_sha(record),
        "actual_record_sha256": record_sha(record),
        "current_revision": REVISION,
        "expected_deployment_scope_sha256": DEPLOYMENT_DIGEST,
        "record_is_external": True,
        "now": NOW,
        "repository": repository_state(),
    }
    options.update(overrides)
    return evaluate_change_preflight(**options), options


def test_valid_external_record_passes_with_strict_hold_evidence():
    lines, options = evaluate()
    payload = build_preflight_evidence(
        **options,
        approval_request=approval_request(),
        readiness=readiness_payload(),
    )
    result = ProductionShadowChangePreflightEvidencePolicy().evaluate(payload)

    assert lines == PASS_LINES
    assert lines == (
        "PRODUCTION_BUDGET_SHADOW_CHANGE_PREFLIGHT=PASS",
        "EXTERNAL_APPROVAL_RECORD=VERIFIED",
        "REQUESTED_PHASE=BUDGET_SHADOW_ONLY",
        "CONFIGURATION_CHANGED=false",
        "PRINCIPAL_WRITE_SHADOW_PRODUCTION=NOT_AUTHORIZED",
        "PRINCIPAL_READ_SHADOW_PRODUCTION=NOT_AUTHORIZED",
        "LONG_TERM_MEMORY_CONSUMPTION=BLOCKED",
        "PRODUCTION_OBSERVATION=NOT_RUN",
    )
    assert isinstance(payload, ProductionShadowChangePreflightEvidencePayload)
    assert payload.approval_record_verified is True
    assert payload.configuration_changed is False
    assert payload.deployment_scope_match is True
    assert payload.traffic_percent == 1.0
    assert result.verification_status is VerificationStatus.PASS
    assert result.promotion_decision is PromotionDecision.HOLD


def test_repository_pending_template_is_blocked_and_never_treated_as_approval():
    template = json.loads(
        Path("docs/memory-production-shadow-approval-record.example.json").read_text(
            encoding="utf-8"
        )
    )
    with pytest.raises(ChangePreflightBlocked) as raised:
        evaluate_change_preflight(
            record=template,
            expected_record_sha256=record_sha(template),
            actual_record_sha256=record_sha(template),
            current_revision=REVISION,
            expected_deployment_scope_sha256=DEPLOYMENT_DIGEST,
            record_is_external=False,
            now=NOW,
            repository=repository_state(),
        )

    assert raised.value.codes == (
        "APPROVAL_RECORD_NOT_EXTERNAL",
        "APPROVAL_STATUS_NOT_APPROVED",
    )
    output = format_blocked_output(raised.value.codes)
    assert output[0] == "PRODUCTION_BUDGET_SHADOW_CHANGE_PREFLIGHT=BLOCKED"
    assert "GATE=APPROVAL_RECORD_NOT_EXTERNAL" in output
    assert "GATE=APPROVAL_STATUS_NOT_APPROVED" in output
    assert output[-3:] == (
        "CONFIGURATION_CHANGED=false",
        "LONG_TERM_MEMORY_CONSUMPTION=BLOCKED",
        "PRODUCTION_OBSERVATION=NOT_RUN",
    )
    assert not any("=PASS" in line for line in output)


@pytest.mark.parametrize(
    ("mutator", "override", "code"),
    [
        (
            lambda value: None,
            {"actual_record_sha256": "0" * 64},
            "APPROVAL_RECORD_HASH_MISMATCH",
        ),
        (
            lambda value: value.update({"approved_revision": "9" * 40}),
            {},
            "APPROVED_REVISION_MISMATCH",
        ),
        (
            lambda value: value.update({"deployment_scope_sha256": "8" * 64}),
            {},
            "DEPLOYMENT_SCOPE_MISMATCH",
        ),
        (
            lambda value: value.update({"traffic_percent": 2.0}),
            {},
            "TRAFFIC_PERCENT_EXCEEDS_APPROVAL",
        ),
        (
            lambda value: value.update({"traffic_percent": "1.0"}),
            {},
            "TRAFFIC_PERCENT_EXCEEDS_APPROVAL",
        ),
        (
            lambda value: value.update(
                {"requested_phase": "PRINCIPAL_WRITE_SHADOW"}
            ),
            {},
            "REQUESTED_PHASE_NOT_BUDGET_ONLY",
        ),
        (
            lambda value: value["approvals"]["privacy"].update(
                {"decision": "PENDING"}
            ),
            {},
            "REQUIRED_APPROVAL_NOT_GRANTED",
        ),
        (
            lambda value: value["approvals"].pop("fairness"),
            {},
            "REQUIRED_APPROVAL_NOT_GRANTED",
        ),
        (
            lambda value: value.update(
                {"expires_at": (NOW - timedelta(minutes=1)).isoformat()}
            ),
            {},
            "APPROVAL_RECORD_EXPIRED",
        ),
        (
            lambda value: value.update(
                {"window_end": (NOW + timedelta(hours=1)).isoformat()}
            ),
            {},
            "APPROVED_WINDOW_TOO_SHORT",
        ),
        (
            lambda value: value.update({"unexpected": True}),
            {},
            "APPROVAL_RECORD_FIELDS_INVALID",
        ),
    ],
)
def test_invalid_or_out_of_scope_approval_record_is_blocked(mutator, override, code):
    record = approved_record()
    mutator(record)
    options = {
        "record": record,
        "expected_record_sha256": record_sha(record),
        "actual_record_sha256": record_sha(record),
        "current_revision": REVISION,
        "expected_deployment_scope_sha256": DEPLOYMENT_DIGEST,
        "record_is_external": True,
        "now": NOW,
        "repository": repository_state(),
    }
    options.update(override)

    with pytest.raises(ChangePreflightBlocked) as raised:
        evaluate_change_preflight(**options)

    assert code in raised.value.codes


def test_repository_hard_stop_or_changed_defaults_blocks_valid_record():
    for key, code in (
        ("approval_packet_ready", "APPROVAL_PACKET_NOT_READY"),
        ("readiness_verified", "PRODUCTION_READINESS_UNVERIFIED"),
        ("safe_defaults", "SAFE_DEFAULTS_CHANGED"),
        ("consume_rejected", "CONSUME_NOT_REJECTED"),
        (
            "production_observation_not_run",
            "PRODUCTION_OBSERVATION_ALREADY_STARTED",
        ),
        ("hard_stop_clear", "SHADOW_HARD_STOP_ACTIVE"),
    ):
        state = repository_state()
        state[key] = False
        with pytest.raises(ChangePreflightBlocked) as raised:
            evaluate(repository=state)
        assert code in raised.value.codes


def test_preflight_payload_rejects_private_or_changed_state():
    _, options = evaluate()
    payload = build_preflight_evidence(
        **options,
        approval_request=approval_request(),
        readiness=readiness_payload(),
    )
    private = deepcopy(payload.model_dump(mode="json"))
    private["approver_ref_sha256"] = "3" * 64
    with pytest.raises(ValidationError):
        ProductionShadowChangePreflightEvidencePayload.model_validate(private)

    changed = payload.model_copy(update={"configuration_changed": True})
    result = ProductionShadowChangePreflightEvidencePolicy().evaluate(changed)
    assert result.verification_status is VerificationStatus.BLOCKED
    assert "PREFLIGHT_CONFIGURATION_ALREADY_CHANGED" in result.gate_codes


def test_repository_snapshot_uses_verified_typed_inputs():
    snapshot = repository_snapshot(
        approval_request=approval_request(),
        readiness=readiness_payload(),
    )

    assert snapshot == {
        "approval_packet_ready": True,
        "readiness_verified": True,
        "safe_defaults": True,
        "consume_rejected": True,
        "production_observation_not_run": True,
        "hard_stop_clear": True,
        "configuration_changed": False,
    }


def _write_upstream_bundles(tmp_path, signer):
    request = approval_request()
    request_bundle = EvidenceIssuer(
        signer=signer,
        clock=lambda: NOW,
    ).issue(
        payload_type="production-shadow-approval-request",
        payload=request,
        policy_result=ProductionShadowApprovalRequestPolicy().evaluate(request),
        producer="tests.approval-request",
        tool_version="2.0.0",
        revision="abcdef1",
        scope="memory.production-shadow.approval-request",
    )
    request_path = tmp_path / "approval-request.json"
    AtomicEvidenceWriter().write(request_path, request_bundle)
    readiness_value = readiness_payload()
    readiness_bundle = EvidenceIssuer(
        signer=signer,
        clock=lambda: NOW,
    ).issue(
        payload_type="production-budget-readiness-evidence",
        payload=readiness_value,
        policy_result=ProductionBudgetReadinessEvidencePolicy().evaluate(
            readiness_value
        ),
        producer="tests.readiness",
        tool_version="2.0.0",
        revision="abcdef2",
        scope="memory.production-budget-shadow.readiness",
        input_manifest=(
            input_artifact_from_bundle(
                path=request_path,
                logical_path="production-shadow-approval-request",
                bundle=request_bundle,
            ),
        ),
    )
    readiness_path = tmp_path / "readiness.json"
    AtomicEvidenceWriter().write(readiness_path, readiness_bundle)
    return request_path, readiness_path


def test_cli_binds_protected_inputs_and_external_record(
    monkeypatch,
    tmp_path,
    capsys,
):
    secret = b"c" * 32
    signer = HmacReceiptSigner(key_id="preflight-test", secret=secret)
    request_path, readiness_path = _write_upstream_bundles(tmp_path, signer)
    record = approved_record()
    record_path = tmp_path / "external-approval-record.json"
    record_path.write_text(json.dumps(record), encoding="utf-8")
    output = tmp_path / "preflight.json"
    monkeypatch.setenv("APPROVAL_REQUEST_REVISION", "abcdef1")
    monkeypatch.setenv("READINESS_EVIDENCE_REVISION", "abcdef2")
    monkeypatch.setenv("EVIDENCE_REVISION", "abcdef3")
    monkeypatch.setenv("EVIDENCE_HMAC_KEY_ID", "preflight-test")
    monkeypatch.setenv(
        "EVIDENCE_HMAC_SECRET_B64",
        base64.b64encode(secret).decode("ascii"),
    )

    assert preflight.main(
        [
            "--approval-request",
            str(request_path),
            "--readiness-evidence",
            str(readiness_path),
            "--approval-record",
            str(record_path),
            "--expected-record-sha256",
            record_sha(record),
            "--expected-deployment-scope-sha256",
            DEPLOYMENT_DIGEST,
            "--current-revision",
            REVISION,
            "--now",
            NOW.isoformat(),
            "--output",
            str(output),
        ]
    ) == 0

    verified = EvidenceVerifier(
        registry=EvidenceRegistry.default(),
        receipt_signer=signer,
    ).verify(
        json.loads(output.read_text(encoding="utf-8")),
        expected_revision="abcdef3",
        expected_scope="memory.production-shadow.change-preflight",
    )
    assert isinstance(
        verified.payload,
        ProductionShadowChangePreflightEvidencePayload,
    )
    assert verified.bundle.artifact.verification_status is VerificationStatus.PASS
    assert verified.bundle.artifact.promotion_decision is PromotionDecision.HOLD
    assert len(verified.bundle.artifact.envelope.input_manifest) == 3
    stdout = capsys.readouterr().out
    assert "VERIFICATION_STATUS=PASS" in stdout
    assert "PROMOTION_DECISION=HOLD" in stdout
    assert "EXTERNAL_APPROVAL_RECORD=VERIFIED" in stdout


def test_cli_rejects_readiness_not_bound_to_approval_request(
    monkeypatch,
    tmp_path,
    capsys,
):
    secret = b"c" * 32
    signer = HmacReceiptSigner(key_id="preflight-test", secret=secret)
    request_path, readiness_path = _write_upstream_bundles(tmp_path, signer)
    readiness_value = readiness_payload()
    unbound_bundle = EvidenceIssuer(signer=signer, clock=lambda: NOW).issue(
        payload_type="production-budget-readiness-evidence",
        payload=readiness_value,
        policy_result=ProductionBudgetReadinessEvidencePolicy().evaluate(
            readiness_value
        ),
        producer="tests.unbound-readiness",
        tool_version="2.0.0",
        revision="abcdef2",
        scope="memory.production-budget-shadow.readiness",
    )
    AtomicEvidenceWriter().write(readiness_path, unbound_bundle)
    record = approved_record()
    record_path = tmp_path / "external-approval-record.json"
    record_path.write_text(json.dumps(record), encoding="utf-8")
    output = tmp_path / "preflight.json"
    monkeypatch.setenv("APPROVAL_REQUEST_REVISION", "abcdef1")
    monkeypatch.setenv("READINESS_EVIDENCE_REVISION", "abcdef2")
    monkeypatch.setenv("EVIDENCE_REVISION", "abcdef3")
    monkeypatch.setenv("EVIDENCE_HMAC_KEY_ID", "preflight-test")
    monkeypatch.setenv(
        "EVIDENCE_HMAC_SECRET_B64",
        base64.b64encode(secret).decode("ascii"),
    )

    assert preflight.main(
        [
            "--approval-request",
            str(request_path),
            "--readiness-evidence",
            str(readiness_path),
            "--approval-record",
            str(record_path),
            "--expected-record-sha256",
            record_sha(record),
            "--expected-deployment-scope-sha256",
            DEPLOYMENT_DIGEST,
            "--output",
            str(output),
        ]
    ) == 1
    assert not output.exists()
    assert "GATE=PRODUCTION_PREFLIGHT_INPUT_UNVERIFIED" in capsys.readouterr().out
