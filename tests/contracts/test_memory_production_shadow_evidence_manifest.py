from __future__ import annotations

import base64
from datetime import datetime, timezone
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
    InputArtifact,
    ProductionBudgetAcceptanceEvidencePayload,
    ProductionBudgetReadinessEvidencePayload,
    ProductionBudgetWindowDecisionEvidencePayload,
    ProductionShadowApprovalRequestPayload,
    ProductionShadowChangePreflightEvidencePayload,
    ProductionShadowEvidenceManifestPayload,
    input_artifact_from_bundle,
)
from contracts.evidence.status import PromotionDecision, VerificationStatus
from contracts.policies import (
    ProductionBudgetAcceptanceEvidencePolicy,
    ProductionBudgetObservationEvidencePolicy,
    ProductionBudgetReadinessEvidencePolicy,
    ProductionBudgetWindowDecisionEvidencePolicy,
    ProductionShadowApprovalRequestPolicy,
    ProductionShadowChangePreflightEvidencePolicy,
    ProductionShadowEvidenceManifestPolicy,
)
from scripts import memory_production_shadow_evidence_manifest as manifest_runner
from scripts.memory_production_budget_shadow_acceptance import (
    build_acceptance_payload,
    evaluate_observation,
    observation_record,
)
from scripts.memory_production_budget_shadow_observation import (
    build_observation_payload,
    sanitize_aggregate_input,
)
from scripts.memory_production_shadow_evidence_manifest import (
    ManifestBlocked,
    ManifestSource,
    build_manifest_payload,
    verify_chain,
    verify_sources,
)


FIXTURE = Path(
    "tests/fixtures/memory_production_budget_shadow/pass_candidate.json"
)
SOURCE_REVISION = "a" * 40
REVISIONS = {
    "approval-request": "aaaaaa1",
    "readiness": "aaaaaa2",
    "change-preflight": "aaaaaa3",
    "observation": "aaaaaa4",
    "window-decision": "aaaaaa5",
    "acceptance": "aaaaaa6",
}
SCOPES = {
    "approval-request": "memory.production-shadow.approval-request",
    "readiness": "memory.production-budget-shadow.readiness",
    "change-preflight": "memory.production-shadow.change-preflight",
    "observation": "memory.production-budget-shadow.observation",
    "window-decision": "memory.production-budget-shadow.window-decision",
    "acceptance": "memory.production-budget-shadow.acceptance",
}


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
        validated_revision=SOURCE_REVISION,
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


def preflight_payload() -> ProductionShadowChangePreflightEvidencePayload:
    return ProductionShadowChangePreflightEvidencePayload(
        schema_version="production-shadow-change-preflight-evidence-v1",
        validated_revision=SOURCE_REVISION,
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


def window_payload() -> ProductionBudgetWindowDecisionEvidencePayload:
    return ProductionBudgetWindowDecisionEvidencePayload(
        schema_version="production-budget-window-decision-evidence-v1",
        source_preflight_verified=True,
        current_state="CLOSED",
        action="HOLD",
        next_state="CLOSED",
        decision_gate_codes=[],
        configuration_changed=False,
        principal_write_shadow_production="NOT_AUTHORIZED",
        principal_read_shadow_production="NOT_AUTHORIZED",
        long_term_memory_consumption="BLOCKED",
        synthetic=True,
    )


def external_item(path: str, character: str) -> InputArtifact:
    return InputArtifact(
        path=path,
        sha256=character * 64,
        receipt_sha256=character * 64,
        size_bytes=1,
        media_type="application/json",
    )


def issue_to_path(
    *,
    signer,
    tmp_path,
    logical_name,
    payload_type,
    payload,
    policy_result,
    input_manifest=(),
):
    bundle = EvidenceIssuer(
        signer=signer,
        clock=lambda: datetime.now(timezone.utc),
    ).issue(
        payload_type=payload_type,
        payload=payload,
        policy_result=policy_result,
        producer=f"tests.{logical_name}",
        tool_version="2.0.0",
        revision=REVISIONS[logical_name],
        scope=SCOPES[logical_name],
        input_manifest=input_manifest,
    )
    path = tmp_path / f"{logical_name}.json"
    AtomicEvidenceWriter().write(path, bundle)
    return path, bundle


def protected_chain(tmp_path, signer):
    paths = {}
    bundles = {}
    request = approval_request()
    paths["approval-request"], bundles["approval-request"] = issue_to_path(
        signer=signer,
        tmp_path=tmp_path,
        logical_name="approval-request",
        payload_type="production-shadow-approval-request",
        payload=request,
        policy_result=ProductionShadowApprovalRequestPolicy().evaluate(request),
    )
    readiness = readiness_payload()
    paths["readiness"], bundles["readiness"] = issue_to_path(
        signer=signer,
        tmp_path=tmp_path,
        logical_name="readiness",
        payload_type="production-budget-readiness-evidence",
        payload=readiness,
        policy_result=ProductionBudgetReadinessEvidencePolicy().evaluate(readiness),
        input_manifest=(
            input_artifact_from_bundle(
                path=paths["approval-request"],
                logical_path="production-shadow-approval-request",
                bundle=bundles["approval-request"],
            ),
        ),
    )
    preflight = preflight_payload()
    paths["change-preflight"], bundles["change-preflight"] = issue_to_path(
        signer=signer,
        tmp_path=tmp_path,
        logical_name="change-preflight",
        payload_type="production-shadow-change-preflight-evidence",
        payload=preflight,
        policy_result=ProductionShadowChangePreflightEvidencePolicy().evaluate(
            preflight
        ),
        input_manifest=(
            input_artifact_from_bundle(
                path=paths["approval-request"],
                logical_path="production-shadow-approval-request",
                bundle=bundles["approval-request"],
            ),
            input_artifact_from_bundle(
                path=paths["readiness"],
                logical_path="production-budget-readiness-evidence",
                bundle=bundles["readiness"],
            ),
            external_item("external-production-shadow-approval-record", "1"),
        ),
    )
    aggregate = json.loads(FIXTURE.read_text(encoding="utf-8"))
    observation = build_observation_payload(
        sanitize_aggregate_input(aggregate),
        preflight=preflight,
    )
    paths["observation"], bundles["observation"] = issue_to_path(
        signer=signer,
        tmp_path=tmp_path,
        logical_name="observation",
        payload_type="production-budget-observation-evidence",
        payload=observation,
        policy_result=ProductionBudgetObservationEvidencePolicy().evaluate(
            observation
        ),
        input_manifest=(
            input_artifact_from_bundle(
                path=paths["change-preflight"],
                logical_path="production-shadow-change-preflight-evidence",
                bundle=bundles["change-preflight"],
            ),
            external_item("external-production-budget-aggregate", "2"),
        ),
    )
    window = window_payload()
    paths["window-decision"], bundles["window-decision"] = issue_to_path(
        signer=signer,
        tmp_path=tmp_path,
        logical_name="window-decision",
        payload_type="production-budget-window-decision-evidence",
        payload=window,
        policy_result=ProductionBudgetWindowDecisionEvidencePolicy().evaluate(window),
        input_manifest=(
            input_artifact_from_bundle(
                path=paths["change-preflight"],
                logical_path="production-shadow-change-preflight-evidence",
                bundle=bundles["change-preflight"],
            ),
            external_item("external-production-budget-window-state", "3"),
        ),
    )
    observation_value = observation_record(observation)
    decision = evaluate_observation(observation_value)
    acceptance = build_acceptance_payload(
        decision,
        observation_value,
        observation=observation,
        window=window,
    )
    paths["acceptance"], bundles["acceptance"] = issue_to_path(
        signer=signer,
        tmp_path=tmp_path,
        logical_name="acceptance",
        payload_type="production-budget-acceptance-evidence",
        payload=acceptance,
        policy_result=ProductionBudgetAcceptanceEvidencePolicy().evaluate(
            acceptance
        ),
        input_manifest=(
            input_artifact_from_bundle(
                path=paths["observation"],
                logical_path="production-budget-observation-evidence",
                bundle=bundles["observation"],
            ),
            input_artifact_from_bundle(
                path=paths["window-decision"],
                logical_path="production-budget-window-decision-evidence",
                bundle=bundles["window-decision"],
            ),
        ),
    )
    return paths, bundles


def manifest_sources(paths):
    return (
        ManifestSource(
            "approval-request",
            paths["approval-request"],
            REVISIONS["approval-request"],
            SCOPES["approval-request"],
            ProductionShadowApprovalRequestPayload,
        ),
        ManifestSource(
            "readiness",
            paths["readiness"],
            REVISIONS["readiness"],
            SCOPES["readiness"],
            ProductionBudgetReadinessEvidencePayload,
        ),
        ManifestSource(
            "change-preflight",
            paths["change-preflight"],
            REVISIONS["change-preflight"],
            SCOPES["change-preflight"],
            ProductionShadowChangePreflightEvidencePayload,
        ),
        ManifestSource(
            "observation",
            paths["observation"],
            REVISIONS["observation"],
            SCOPES["observation"],
            type(build_observation_payload(
                sanitize_aggregate_input(
                    json.loads(FIXTURE.read_text(encoding="utf-8"))
                ),
                preflight=preflight_payload(),
            )),
        ),
        ManifestSource(
            "window-decision",
            paths["window-decision"],
            REVISIONS["window-decision"],
            SCOPES["window-decision"],
            ProductionBudgetWindowDecisionEvidencePayload,
        ),
        ManifestSource(
            "acceptance",
            paths["acceptance"],
            REVISIONS["acceptance"],
            SCOPES["acceptance"],
            ProductionBudgetAcceptanceEvidencePayload,
        ),
    )


def test_verified_chain_builds_strict_synthetic_hold_manifest(tmp_path):
    signer = HmacReceiptSigner(key_id="manifest-test", secret=b"m" * 32)
    paths, _ = protected_chain(tmp_path, signer)
    verified = verify_sources(
        manifest_sources(paths),
        verifier=EvidenceVerifier(
            registry=EvidenceRegistry.default(),
            receipt_signer=signer,
        ),
    )
    payload = build_manifest_payload(verified, source_revision=SOURCE_REVISION)
    result = ProductionShadowEvidenceManifestPolicy().evaluate(payload)

    assert isinstance(payload, ProductionShadowEvidenceManifestPayload)
    assert payload.artifact_count == 6
    assert payload.chain_bound is True
    assert payload.final_acceptance_status == "PASS"
    assert payload.synthetic is True
    assert result.verification_status is VerificationStatus.PASS
    assert result.promotion_decision is PromotionDecision.HOLD


def test_same_type_but_unbound_readiness_is_rejected(tmp_path):
    signer = HmacReceiptSigner(key_id="manifest-test", secret=b"m" * 32)
    paths, _ = protected_chain(tmp_path, signer)
    readiness = readiness_payload()
    unbound = EvidenceIssuer(signer=signer).issue(
        payload_type="production-budget-readiness-evidence",
        payload=readiness,
        policy_result=ProductionBudgetReadinessEvidencePolicy().evaluate(readiness),
        producer="tests.unbound-readiness",
        tool_version="2.0.0",
        revision=REVISIONS["readiness"],
        scope=SCOPES["readiness"],
    )
    AtomicEvidenceWriter().write(paths["readiness"], unbound)
    verified = verify_sources(
        manifest_sources(paths),
        verifier=EvidenceVerifier(
            registry=EvidenceRegistry.default(),
            receipt_signer=signer,
        ),
    )

    with pytest.raises(ManifestBlocked) as raised:
        verify_chain(verified)

    assert "MANIFEST_READINESS_CHAIN_UNBOUND" in raised.value.codes


def test_manifest_payload_rejects_unknown_fields(tmp_path):
    signer = HmacReceiptSigner(key_id="manifest-test", secret=b"m" * 32)
    paths, _ = protected_chain(tmp_path, signer)
    verified = verify_sources(
        manifest_sources(paths),
        verifier=EvidenceVerifier(
            registry=EvidenceRegistry.default(),
            receipt_signer=signer,
        ),
    )
    value = build_manifest_payload(
        verified,
        source_revision=SOURCE_REVISION,
    ).model_dump(mode="json")
    value["principal_id"] = "private"

    with pytest.raises(ValidationError):
        ProductionShadowEvidenceManifestPayload.model_validate(value)


def test_cli_writes_verified_manifest_with_six_input_receipts(
    monkeypatch,
    tmp_path,
    capsys,
):
    secret = b"m" * 32
    signer = HmacReceiptSigner(key_id="manifest-test", secret=secret)
    paths, _ = protected_chain(tmp_path, signer)
    output = tmp_path / "manifest.json"
    env_names = {
        "APPROVAL_REQUEST_REVISION": "approval-request",
        "READINESS_EVIDENCE_REVISION": "readiness",
        "PREFLIGHT_EVIDENCE_REVISION": "change-preflight",
        "OBSERVATION_EVIDENCE_REVISION": "observation",
        "WINDOW_EVIDENCE_REVISION": "window-decision",
        "ACCEPTANCE_EVIDENCE_REVISION": "acceptance",
    }
    for env_name, logical_name in env_names.items():
        monkeypatch.setenv(env_name, REVISIONS[logical_name])
    monkeypatch.setenv("EVIDENCE_REVISION", "aaaaaa7")
    monkeypatch.setenv("EVIDENCE_HMAC_KEY_ID", "manifest-test")
    monkeypatch.setenv(
        "EVIDENCE_HMAC_SECRET_B64",
        base64.b64encode(secret).decode("ascii"),
    )

    assert manifest_runner.main(
        [
            "--approval-request",
            str(paths["approval-request"]),
            "--readiness-evidence",
            str(paths["readiness"]),
            "--preflight-evidence",
            str(paths["change-preflight"]),
            "--observation-evidence",
            str(paths["observation"]),
            "--window-evidence",
            str(paths["window-decision"]),
            "--acceptance-evidence",
            str(paths["acceptance"]),
            "--source-revision",
            SOURCE_REVISION,
            "--output",
            str(output),
        ]
    ) == 0

    verified = EvidenceVerifier(
        registry=EvidenceRegistry.default(),
        receipt_signer=signer,
    ).verify(
        json.loads(output.read_text(encoding="utf-8")),
        expected_revision="aaaaaa7",
        expected_scope="memory.production-shadow.evidence-manifest",
    )
    assert isinstance(verified.payload, ProductionShadowEvidenceManifestPayload)
    assert verified.payload.artifact_count == 6
    assert verified.bundle.artifact.verification_status is VerificationStatus.PASS
    assert verified.bundle.artifact.promotion_decision is PromotionDecision.HOLD
    assert len(verified.bundle.artifact.envelope.input_manifest) == 6
    stdout = capsys.readouterr().out
    assert "MEMORY_PRODUCTION_SHADOW_EVIDENCE_MANIFEST=VERIFIED" in stdout
    assert "PROMOTION_DECISION=HOLD" in stdout
