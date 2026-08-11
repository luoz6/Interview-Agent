from __future__ import annotations

import base64
from datetime import datetime, timezone
import json

from contracts.evidence import (
    AtomicEvidenceWriter,
    CleanupRecord,
    EvidenceIssuer,
    EvidenceRegistry,
    EvidenceVerifier,
    HmacReceiptSigner,
    ReleaseEvidencePayload,
)
from contracts.evidence.digest import sha256_bytes
from contracts.policies import CleanupEvidencePolicy, ReleaseEvidencePolicy
from scripts import memory_cleanup_evidence as cleanup


def release_evidence(path, signer):
    payload = ReleaseEvidencePayload(
        schema_version="release-evidence-v1",
        changed_path_count=0,
        staged_path_count=0,
        clean_detached_worktree=True,
        shadow_modes_changed=False,
        blockers=[],
        synthetic=True,
    )
    bundle = EvidenceIssuer(
        signer=signer,
        clock=lambda: datetime.now(timezone.utc),
    ).issue(
        payload_type="release-evidence",
        payload=payload,
        policy_result=ReleaseEvidencePolicy().evaluate(payload),
        producer="tests.release",
        tool_version="1.0.0",
        revision="abcdef1",
        scope="memory.shadow.release-preflight",
    )
    AtomicEvidenceWriter().write(path, bundle)


def cleanup_record(*, residue_count=0):
    return {
        "schema_version": "cleanup-record-v1",
        "validated_revision": "abcdef1",
        "target_fingerprint": "1" * 64,
        "ownership_verified": True,
        "resources_examined": 4,
        "resources_removed": 4,
        "residue_count": residue_count,
        "synthetic": True,
    }


def test_cleanup_policy_holds_synthetic_zero_residue_evidence():
    record = CleanupRecord.model_validate(cleanup_record())
    payload = cleanup.build_cleanup_evidence(record)

    result = CleanupEvidencePolicy().evaluate(payload)

    assert result.verification_status.value == "PASS"
    assert result.promotion_decision.value == "HOLD"


def test_cli_writes_signed_cleanup_evidence(monkeypatch, tmp_path, capsys):
    secret = b"c" * 32
    signer = HmacReceiptSigner(key_id="cleanup-test", secret=secret)
    release_path = tmp_path / "release.json"
    release_evidence(release_path, signer)
    record_path = tmp_path / "cleanup-record.json"
    record_path.write_text(
        json.dumps(cleanup_record(), sort_keys=True),
        encoding="utf-8",
    )
    expected_record_sha256 = sha256_bytes(record_path.read_bytes())
    output = tmp_path / "cleanup-evidence.json"
    monkeypatch.setenv("EVIDENCE_REVISION", "abcdef1")
    monkeypatch.setenv("EVIDENCE_HMAC_KEY_ID", "cleanup-test")
    monkeypatch.setenv(
        "EVIDENCE_HMAC_SECRET_B64",
        base64.b64encode(secret).decode("ascii"),
    )

    assert cleanup.main(
        [
            "--release-evidence",
            str(release_path),
            "--release-revision",
            "abcdef1",
            "--cleanup-record",
            str(record_path),
            "--expected-record-sha256",
            expected_record_sha256,
            "--expected-target-fingerprint",
            "1" * 64,
            "--output",
            str(output),
        ]
    ) == 0

    verified = EvidenceVerifier(
        registry=EvidenceRegistry.default(),
        receipt_signer=signer,
    ).verify(
        json.loads(output.read_text(encoding="utf-8")),
        expected_revision="abcdef1",
        expected_scope="memory.cleanup.evidence",
    )
    assert verified.bundle.artifact.payload_type == "cleanup-evidence"
    assert verified.bundle.artifact.verification_status.value == "PASS"
    assert verified.bundle.artifact.promotion_decision.value == "HOLD"
    assert [
        item.path for item in verified.bundle.artifact.envelope.input_manifest
    ] == ["release-preflight-evidence", "external-cleanup-record"]
    stdout = capsys.readouterr().out
    assert "VERIFICATION_STATUS=PASS" in stdout
    assert "PROMOTION_DECISION=HOLD" in stdout


def test_cli_rejects_target_fingerprint_mismatch(monkeypatch, tmp_path):
    secret = b"c" * 32
    signer = HmacReceiptSigner(key_id="cleanup-test", secret=secret)
    release_path = tmp_path / "release.json"
    release_evidence(release_path, signer)
    record_path = tmp_path / "cleanup-record.json"
    record_path.write_text(json.dumps(cleanup_record()), encoding="utf-8")
    monkeypatch.setenv("EVIDENCE_REVISION", "abcdef1")
    monkeypatch.setenv("EVIDENCE_HMAC_KEY_ID", "cleanup-test")
    monkeypatch.setenv(
        "EVIDENCE_HMAC_SECRET_B64",
        base64.b64encode(secret).decode("ascii"),
    )

    assert cleanup.main(
        [
            "--release-evidence",
            str(release_path),
            "--release-revision",
            "abcdef1",
            "--cleanup-record",
            str(record_path),
            "--expected-record-sha256",
            sha256_bytes(record_path.read_bytes()),
            "--expected-target-fingerprint",
            "2" * 64,
            "--output",
            str(tmp_path / "cleanup-evidence.json"),
        ]
    ) == 1
