import json
import base64
import hashlib
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.services.independent_review_handoff import (
    ATTESTATION_SCHEMA_VERSION,
    DETACHED_SIGNATURE_SCHEMA_VERSION,
    FREEZE_AUTHORIZATION_SCHEMA_VERSION,
    IDENTITY_RECEIPT_SCHEMA_VERSION,
    CoordinatorFreezeAuthorization,
    DetachedSignatureEvidence,
    DomainResultRecord,
    ExternalIdentityReceipt,
    ReviewerIndependenceAttestation,
    append_handoff_event,
    canonical_sha256,
    empty_handoff_ledger,
    export_reviewer_handoff,
    freeze_review_sheet,
    make_domain_result,
    make_unknown_domain_result,
    safe_repo_relative_path,
)
from scripts.generate_independent_review_artifacts import main as export_main


NOW = datetime(2026, 8, 7, 10, 0, tzinfo=timezone.utc)


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _workspace(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    workspace = tmp_path / "workspace"
    packet = {
        "schema_version": "test-review-packet-v1",
        "dataset_id": "synthetic-review-data",
        "pairs": [{"pair_id": "pair-1", "A": "one", "B": "two"}],
    }
    packet_hash = canonical_sha256(packet)
    protocol = workspace / "docs/review-protocol.md"
    protocol.parent.mkdir(parents=True, exist_ok=True)
    protocol.write_text("# Independent review protocol\n", encoding="utf-8")
    _write_json(workspace / "reports/reviewer/packet.json", packet)
    _write_json(
        workspace / "reports/reviewer/empty-review-sheet.json",
        {
            "protocol_version": "test-review-v1",
            "packet_sha256": packet_hash,
            "judgments": [],
        },
    )
    _write_json(
        workspace / "reports/dataset-validation.json",
        {"status": "PASS", "sample_size": 1},
    )
    return workspace, {
        "protocol": "docs/review-protocol.md",
        "packet": "reports/reviewer/packet.json",
        "empty_sheet": "reports/reviewer/empty-review-sheet.json",
        "public_validation": "reports/dataset-validation.json",
    }


def _manifest(tmp_path: Path):
    workspace, sources = _workspace(tmp_path)
    return export_reviewer_handoff(
        workspace_root=workspace,
        staging_dir=tmp_path / "staging",
        review_kind="gate2_calibration",
        handoff_id="gate2-review-1",
        created_at=NOW,
        sources=sources,
    )


def _attestation(manifest, **updates):
    payload = {
        "schema_version": ATTESTATION_SCHEMA_VERSION,
        "attestation_id": "attestation-1",
        "review_kind": manifest.review_kind,
        "reviewer_id": "reviewer-042",
        "reviewer_kind": "human",
        "reviewer_role": "independent_technical_reviewer",
        "packet_sha256": manifest.packet_canonical_sha256,
        "protocol_sha256": manifest.protocol_raw_sha256,
        "no_implementation_involvement": True,
        "no_dataset_annotation_involvement": True,
        "no_coordinator_key_access": True,
        "assignment_was_hidden": True,
        "no_conflict_of_interest": True,
        "declared_conflicts": [],
        "human_identity_verified_by_coordinator": True,
        "attested_at": NOW,
        "attestation_text_sha256": "a" * 64,
    }
    payload.update(updates)
    return ReviewerIndependenceAttestation(
        **payload,
        attestation_sha256=canonical_sha256(payload),
    )


def _public_pem(key: Ed25519PrivateKey) -> bytes:
    return key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )


def _public_key_sha256(key: Ed25519PrivateKey) -> str:
    raw = key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return hashlib.sha256(raw).hexdigest()


def _signature(
    key: Ed25519PrivateKey,
    *,
    signature_id: str,
    authority_id: str,
    artifact_sha256: str,
    synthetic_fixture: bool = True,
):
    payload = {
        "schema_version": DETACHED_SIGNATURE_SCHEMA_VERSION,
        "signature_id": signature_id,
        "signer_authority_id": authority_id,
        "signed_artifact_sha256": artifact_sha256,
        "algorithm": "ed25519-sha256-binding-v1",
        "public_key_sha256": _public_key_sha256(key),
        "signature_base64": base64.b64encode(
            key.sign(bytes.fromhex(artifact_sha256))
        ).decode("ascii"),
        "signed_at": NOW,
        "synthetic_fixture": synthetic_fixture,
    }
    return DetachedSignatureEvidence(
        **payload,
        signature_record_sha256=canonical_sha256(payload),
    )


def _receipt(
    tmp_path: Path,
    manifest,
    attestation,
    *,
    received_at=NOW,
    frozen_at=NOW,
    empty_sheet=False,
    same_authority=False,
):
    sheet_path = tmp_path / "completed-sheet.json"
    _write_json(
        sheet_path,
        {
            "protocol_version": "test-review-v1",
            "packet_sha256": manifest.packet_canonical_sha256,
            "judgments": (
                [] if empty_sheet else [{"pair_id": "pair-1", "score": 4}]
            ),
        },
    )
    sheet = json.loads(sheet_path.read_text(encoding="utf-8"))
    sheet_sha256 = canonical_sha256(sheet)
    identity_key = Ed25519PrivateKey.generate()
    reviewer_key = Ed25519PrivateKey.generate()
    coordinator_key = Ed25519PrivateKey.generate()
    reviewer_authority_id = "reviewer-signing-authority-042"
    coordinator_authority_id = (
        reviewer_authority_id if same_authority else "coordinator-authority-1"
    )
    identity_payload = {
        "schema_version": IDENTITY_RECEIPT_SCHEMA_VERSION,
        "identity_receipt_id": "identity-receipt-1",
        "review_kind": manifest.review_kind,
        "reviewer_id": attestation.reviewer_id,
        "identity_authority_id": "external-identity-authority-1",
        "packet_sha256": manifest.packet_canonical_sha256,
        "identity_subject_sha256": "c" * 64,
        "human_identity_verified": True,
        "evidence_origin": "synthetic_fixture",
        "synthetic_fixture": True,
        "issued_at": NOW,
        "expires_at": NOW + timedelta(days=1),
    }
    identity_receipt = ExternalIdentityReceipt(
        **identity_payload,
        receipt_payload_sha256=canonical_sha256(identity_payload),
    )
    identity_signature = _signature(
        identity_key,
        signature_id="identity-signature-1",
        authority_id=identity_receipt.identity_authority_id,
        artifact_sha256=identity_receipt.receipt_payload_sha256,
    )
    reviewer_signature = _signature(
        reviewer_key,
        signature_id="reviewer-sheet-signature-1",
        authority_id=reviewer_authority_id,
        artifact_sha256=sheet_sha256,
    )
    authorization_payload = {
        "schema_version": FREEZE_AUTHORIZATION_SCHEMA_VERSION,
        "authorization_id": "freeze-authorization-1",
        "handoff_id": manifest.handoff_id,
        "review_kind": manifest.review_kind,
        "reviewer_id": attestation.reviewer_id,
        "reviewer_authority_id": reviewer_authority_id,
        "coordinator_authority_id": coordinator_authority_id,
        "manifest_sha256": manifest.manifest_sha256,
        "packet_sha256": manifest.packet_canonical_sha256,
        "sheet_canonical_sha256": sheet_sha256,
        "attestation_sha256": attestation.attestation_sha256,
        "identity_receipt_sha256": identity_receipt.receipt_payload_sha256,
        "reviewer_signature_record_sha256": (
            reviewer_signature.signature_record_sha256
        ),
        "received_at": received_at,
        "frozen_at": frozen_at,
        "synthetic_fixture": True,
    }
    freeze_authorization = CoordinatorFreezeAuthorization(
        **authorization_payload,
        authorization_payload_sha256=canonical_sha256(authorization_payload),
    )
    coordinator_signature = _signature(
        coordinator_key,
        signature_id="coordinator-freeze-signature-1",
        authority_id=coordinator_authority_id,
        artifact_sha256=freeze_authorization.authorization_payload_sha256,
    )
    return freeze_review_sheet(
        manifest=manifest,
        sheet_path=sheet_path,
        attestation=attestation,
        identity_receipt=identity_receipt,
        identity_signature=identity_signature,
        identity_public_key_pem=_public_pem(identity_key),
        reviewer_signature=reviewer_signature,
        reviewer_public_key_pem=_public_pem(reviewer_key),
        freeze_authorization=freeze_authorization,
        coordinator_signature=coordinator_signature,
        coordinator_public_key_pem=_public_pem(coordinator_key),
        receipt_id="receipt-1",
        coordinator_id=coordinator_authority_id,
        received_at=received_at,
        frozen_at=frozen_at,
    )


def test_empty_staging_export_is_allowlisted_and_hash_bound(tmp_path):
    workspace, sources = _workspace(tmp_path)
    staging = tmp_path / "staging"
    manifest = export_reviewer_handoff(
        workspace_root=workspace,
        staging_dir=staging,
        review_kind="t49_semantic",
        handoff_id="t49-review-1",
        created_at=NOW,
        sources=sources,
    )

    exported = sorted(
        path.relative_to(staging).as_posix()
        for path in staging.rglob("*")
        if path.is_file()
    )
    assert exported == [
        "handoff-manifest.json",
        "protocol.md",
        "public/dataset-validation.json",
        "reviewer/empty-review-sheet.json",
        "reviewer/packet.json",
    ]
    assert manifest.packet_canonical_sha256 == canonical_sha256(
        json.loads((staging / "reviewer/packet.json").read_text(encoding="utf-8"))
    )
    assert json.loads(
        (staging / "reviewer/empty-review-sheet.json").read_text(encoding="utf-8")
    )["judgments"] == []
    assert not any("coordinator" in path.casefold() for path in exported)


def test_export_requires_an_empty_staging_directory(tmp_path):
    workspace, sources = _workspace(tmp_path)
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "unexpected.txt").write_text("occupied", encoding="utf-8")

    with pytest.raises(ValueError, match="STAGING_DIRECTORY_NOT_EMPTY"):
        export_reviewer_handoff(
            workspace_root=workspace,
            staging_dir=staging,
            review_kind="gate3_dataset",
            handoff_id="gate3-review-1",
            created_at=NOW,
            sources=sources,
        )


@pytest.mark.parametrize(
    "path,error",
    [
        ("../assignment-key.json", "UNSAFE_REPO_RELATIVE_PATH"),
        ("C:/private/packet.json", "UNSAFE_REPO_RELATIVE_PATH"),
        (
            "reports/coordinator-only/assignment-key.json",
            "COORDINATOR_ONLY_PATH_PROHIBITED",
        ),
        ("reports/reviewer/api-key.json", "SENSITIVE_FILENAME_PROHIBITED"),
    ],
)
def test_safe_repo_relative_path_rejects_private_or_machine_paths(path, error):
    with pytest.raises(ValueError, match=error):
        safe_repo_relative_path(path)


def test_export_rejects_secret_json_without_copying_it(tmp_path):
    workspace, sources = _workspace(tmp_path)
    _write_json(
        workspace / sources["packet"],
        {"dataset_id": "synthetic", "api_key": "not-a-real-key"},
    )
    staging = tmp_path / "staging"

    with pytest.raises(ValueError, match="PROHIBITED_PUBLIC_JSON_KEY"):
        export_reviewer_handoff(
            workspace_root=workspace,
            staging_dir=staging,
            review_kind="gate2_calibration",
            handoff_id="gate2-review-1",
            created_at=NOW,
            sources=sources,
        )
    assert not staging.exists()


def test_export_rejects_machine_path_values_and_coordinator_sources(tmp_path):
    workspace, sources = _workspace(tmp_path)
    _write_json(
        workspace / sources["public_validation"],
        {"status": "PASS", "machine_path": "C:/Users/operator/private.json"},
    )
    with pytest.raises(ValueError, match="MACHINE_PATH_PROHIBITED"):
        export_reviewer_handoff(
            workspace_root=workspace,
            staging_dir=tmp_path / "machine-path-staging",
            review_kind="gate3_dataset",
            handoff_id="gate3-review-1",
            created_at=NOW,
            sources=sources,
        )

    sources["packet"] = "reports/coordinator-only/assignment-key.json"
    coordinator_staging = tmp_path / "coordinator-staging"
    with pytest.raises(ValueError, match="COORDINATOR_ONLY_PATH_PROHIBITED"):
        export_reviewer_handoff(
            workspace_root=workspace,
            staging_dir=coordinator_staging,
            review_kind="gate3_dataset",
            handoff_id="gate3-review-1",
            created_at=NOW,
            sources=sources,
        )
    assert not coordinator_staging.exists()


@pytest.mark.parametrize(
    "content,error",
    [
        ("Connect to postgresql://db.internal/review", "CONNECTION_URI"),
        ("-----BEGIN PRIVATE KEY-----\nabc", "PRIVATE_KEY"),
        ("api_key=synthetic-but-prohibited", "API_KEY"),
        ("Read C:/Users/operator/private.txt", "MACHINE_PATH"),
        ("Ask for the coordinator-only mapping", "PRIVATE_COORDINATOR_CONTENT"),
        ("Unblind after completion", "PRIVATE_COORDINATOR_CONTENT"),
    ],
)
def test_protocol_content_is_scanned_before_export(tmp_path, content, error):
    workspace, sources = _workspace(tmp_path)
    (workspace / sources["protocol"]).write_text(content, encoding="utf-8")
    staging = tmp_path / "protocol-staging"
    with pytest.raises(ValueError, match=error):
        export_reviewer_handoff(
            workspace_root=workspace,
            staging_dir=staging,
            review_kind="gate2_calibration",
            handoff_id="gate2-review-1",
            created_at=NOW,
            sources=sources,
        )
    assert not staging.exists()


def test_each_source_component_is_checked_for_reparse_points(
    tmp_path, monkeypatch
):
    workspace, sources = _workspace(tmp_path)
    import app.services.independent_review_handoff as handoff

    original = handoff._has_reparse_point

    def simulated_reparse(path):
        return path.name == "reviewer" or original(path)

    monkeypatch.setattr(handoff, "_has_reparse_point", simulated_reparse)
    with pytest.raises(ValueError, match="REPARSE_POINT_SOURCE_PROHIBITED"):
        export_reviewer_handoff(
            workspace_root=workspace,
            staging_dir=tmp_path / "reparse-staging",
            review_kind="gate3_dataset",
            handoff_id="gate3-review-1",
            created_at=NOW,
            sources=sources,
        )


def test_cli_exports_only_the_reviewer_allowlist(tmp_path, monkeypatch, capsys):
    workspace, sources = _workspace(tmp_path)
    staging = tmp_path / "cli-staging"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "generate_independent_review_artifacts.py",
            "--workspace-root",
            str(workspace),
            "--staging-dir",
            str(staging),
            "--review-kind",
            "gate2_calibration",
            "--handoff-id",
            "gate2-cli-review",
            "--created-at",
            "2026-08-07T10:00:00Z",
            "--protocol",
            sources["protocol"],
            "--packet",
            sources["packet"],
            "--empty-sheet",
            sources["empty_sheet"],
        ],
    )

    assert export_main() == 0
    output = json.loads(capsys.readouterr().out)
    assert output["handoff_id"] == "gate2-cli-review"
    assert sorted(
        path.relative_to(staging).as_posix()
        for path in staging.rglob("*")
        if path.is_file()
    ) == [
        "handoff-manifest.json",
        "protocol.md",
        "reviewer/empty-review-sheet.json",
        "reviewer/packet.json",
    ]


def test_agent_or_conflicted_reviewer_cannot_be_attested_as_human(tmp_path):
    manifest = _manifest(tmp_path)
    with pytest.raises(ValidationError, match="NON_HUMAN_REVIEWER_PROHIBITED"):
        _attestation(manifest, reviewer_id="codex-agent-reviewer")
    with pytest.raises(ValidationError, match="REVIEWER_CONFLICT_DECLARED"):
        _attestation(
            manifest,
            declared_conflicts=["implemented the scoring change"],
        )


def test_sheet_receipt_binds_packet_attestation_and_freeze_time(tmp_path):
    manifest = _manifest(tmp_path)
    attestation = _attestation(manifest)
    receipt = _receipt(tmp_path, manifest, attestation)

    assert receipt.packet_sha256 == manifest.packet_canonical_sha256
    assert receipt.attestation_sha256 == attestation.attestation_sha256
    assert receipt.received_at == receipt.frozen_at == NOW
    assert receipt.judgment_count == 1
    assert receipt.detached_signatures_verified is True
    assert receipt.dual_authority_verified is True
    assert receipt.synthetic_fixture is True
    assert receipt.gate_evidence_ready is False

    bad = _attestation(manifest, packet_sha256="0" * 64)
    with pytest.raises(ValueError, match="ATTESTATION_PACKET_HASH_MISMATCH"):
        _receipt(tmp_path, manifest, bad)

    late_attestation = _attestation(
        manifest,
        attested_at=NOW + timedelta(seconds=1),
    )
    with pytest.raises(ValueError, match="ATTESTATION_AFTER_SHEET_RECEIPT"):
        _receipt(tmp_path, manifest, late_attestation)

    with pytest.raises(ValueError, match="EMPTY_FROZEN_REVIEW_SHEET_PROHIBITED"):
        _receipt(tmp_path, manifest, attestation, empty_sheet=True)
    with pytest.raises(
        ValidationError, match="REVIEWER_COORDINATOR_AUTHORITY_COLLISION"
    ):
        _receipt(tmp_path, manifest, attestation, same_authority=True)


def test_self_declared_human_attestation_cannot_replace_external_evidence(tmp_path):
    manifest = _manifest(tmp_path)
    attestation = _attestation(manifest)
    sheet_path = tmp_path / "completed-sheet.json"
    _write_json(
        sheet_path,
        {
            "protocol_version": "test-review-v1",
            "packet_sha256": manifest.packet_canonical_sha256,
            "judgments": [{"pair_id": "pair-1", "score": 4}],
        },
    )

    with pytest.raises(ValueError, match="EXTERNAL_REVIEW_EVIDENCE_REQUIRED"):
        freeze_review_sheet(
            manifest=manifest,
            sheet_path=sheet_path,
            attestation=attestation,
            identity_receipt=None,
            identity_signature=None,
            identity_public_key_pem=b"",
            reviewer_signature=None,
            reviewer_public_key_pem=b"",
            freeze_authorization=None,
            coordinator_signature=None,
            coordinator_public_key_pem=b"",
            receipt_id="self-declared-receipt",
            coordinator_id="self-declared-coordinator",
            received_at=NOW,
            frozen_at=NOW,
        )


def test_unseal_requires_a_frozen_sheet_and_never_reads_key_contents(tmp_path):
    manifest = _manifest(tmp_path)
    receipt = _receipt(tmp_path, manifest, _attestation(manifest))
    ledger = empty_handoff_ledger(manifest)
    ledger = append_handoff_event(
        ledger,
        event_id="exported",
        event_type="HANDOFF_EXPORTED",
        recorded_at=NOW,
    )
    later_receipt = _receipt(
        tmp_path,
        manifest,
        _attestation(manifest),
        received_at=NOW,
        frozen_at=NOW + timedelta(seconds=1),
    )
    with pytest.raises(ValueError, match="HANDOFF_EVENT_BEFORE_SHEET_FREEZE"):
        append_handoff_event(
            ledger,
            event_id="freeze-before-receipt-time",
            event_type="SHEET_FROZEN",
            recorded_at=NOW,
            receipt=later_receipt,
        )

    with pytest.raises(ValueError, match="UNSEAL_BEFORE_SHEET_FREEZE"):
        append_handoff_event(
            ledger,
            event_id="unseal-too-early",
            event_type="UNSEAL_AUTHORIZED",
            recorded_at=NOW,
            receipt=receipt,
        )

    ledger = append_handoff_event(
        ledger,
        event_id="sheet-frozen",
        event_type="SHEET_FROZEN",
        recorded_at=NOW,
        receipt=receipt,
    )
    ledger = append_handoff_event(
        ledger,
        event_id="unseal-authorized",
        event_type="UNSEAL_AUTHORIZED",
        recorded_at=NOW,
        receipt=receipt,
    )
    assert ledger.entries[-1].coordinator_key_contents_read is False
    assert ledger.entries[-1].outcome_status == "UNSEAL_AUTHORIZED"


def test_unknown_gate2_or_gate3_domain_result_is_truthfully_blocked(tmp_path):
    manifest = _manifest(tmp_path)
    receipt = _receipt(tmp_path, manifest, _attestation(manifest))
    ledger = empty_handoff_ledger(manifest)
    for event_id, event_type, event_receipt in (
        ("exported", "HANDOFF_EXPORTED", None),
        ("frozen", "SHEET_FROZEN", receipt),
        ("unseal", "UNSEAL_AUTHORIZED", receipt),
    ):
        ledger = append_handoff_event(
            ledger,
            event_id=event_id,
            event_type=event_type,
            recorded_at=NOW,
            receipt=event_receipt,
        )
    unknown = make_unknown_domain_result(
        result_id="gate2-domain-unknown",
        review_kind=manifest.review_kind,
        recorded_at=NOW,
    )
    ledger = append_handoff_event(
        ledger,
        event_id="domain-unknown",
        event_type="DOMAIN_RESULT_RECORDED",
        recorded_at=NOW,
        domain_result=unknown,
    )

    assert ledger.entries[-1].outcome_status == "BLOCKED_DOMAIN_RESULT_UNKNOWN"

    evaluator_result_path = tmp_path / "synthetic-evaluator-result.json"
    evaluator_result = {
        "evidence_kind": "real_independent_human_review",
        "synthetic_fixture": False,
        "review_receipt_sha256": receipt.receipt_sha256,
        "quality_status": "PASS",
        "human_review_status": "COMPLETE",
        "completed_judgment_count": 1,
        "evaluator_authority_id": "evaluator-authority-1",
    }
    _write_json(evaluator_result_path, evaluator_result)
    evaluator_key = Ed25519PrivateKey.generate()
    evaluator_signature = _signature(
        evaluator_key,
        signature_id="synthetic-evaluator-signature",
        authority_id="evaluator-authority-1",
        artifact_sha256=canonical_sha256(evaluator_result),
    )
    with pytest.raises(ValueError, match="SYNTHETIC_FIXTURE_NOT_GATE_EVIDENCE"):
        make_domain_result(
            result_id="gate2-domain-pass",
            review_kind=manifest.review_kind,
            outcome="PASS",
            evaluator_result_path=evaluator_result_path,
            review_receipt=receipt,
            evaluator_signature=evaluator_signature,
            evaluator_public_key_pem=_public_pem(evaluator_key),
            recorded_at=NOW,
        )


def test_manual_self_hashed_pass_cannot_bypass_external_trust_root(tmp_path):
    manifest = _manifest(tmp_path)
    forged_payload = {
        "schema_version": "independent-review-domain-result-v1",
        "result_id": "forged-self-declared-pass",
        "review_kind": manifest.review_kind,
        "outcome": "PASS",
        "domain_result_sha256": "a" * 64,
        "blocker_code": None,
        "review_receipt_sha256": "b" * 64,
        "evaluator_authority_id": "self-declared-human",
        "evaluator_signature_record_sha256": "c" * 64,
        "synthetic_fixture": False,
        "gate_evidence_ready": True,
        "recorded_at": NOW,
    }
    forged_result = DomainResultRecord(
        **forged_payload,
        record_sha256=canonical_sha256(forged_payload),
    )

    with pytest.raises(
        ValueError, match="EXTERNAL_GATE_AUTHORITY_TRUST_NOT_CONFIGURED"
    ):
        append_handoff_event(
            empty_handoff_ledger(manifest),
            event_id="forged-domain-pass",
            event_type="DOMAIN_RESULT_RECORDED",
            recorded_at=NOW,
            domain_result=forged_result,
        )


def test_append_only_ledger_rejects_tampering(tmp_path):
    manifest = _manifest(tmp_path)
    ledger = append_handoff_event(
        empty_handoff_ledger(manifest),
        event_id="exported",
        event_type="HANDOFF_EXPORTED",
        recorded_at=NOW,
    )
    payload = ledger.model_dump(mode="json")
    payload["entries"][0]["outcome_status"] = "PASS"

    with pytest.raises(ValidationError, match="HANDOFF_LEDGER_ENTRY_HASH_MISMATCH"):
        type(ledger).model_validate(payload)
