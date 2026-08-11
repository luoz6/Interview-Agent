from __future__ import annotations

import base64
from datetime import datetime, timezone
import json

from contracts.evidence import (
    AtomicEvidenceWriter,
    CleanupEvidencePayload,
    EvidenceIssuer,
    EvidenceRegistry,
    EvidenceVerifier,
    HmacReceiptSigner,
    InputArtifact,
    PublicationRecord,
    ReleaseEvidencePayload,
    input_artifact_from_bundle,
)
from contracts.evidence.digest import sha256_bytes
from contracts.policies import (
    CleanupEvidencePolicy,
    PublicationEvidencePolicy,
    ReleaseEvidencePolicy,
)
from scripts import memory_publication_evidence as publication


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
    return bundle


def publication_record():
    return {
        "schema_version": "publication-record-v1",
        "validated_revision": "abcdef1",
        "publication_ref": "refs/tags/interview-agent-v1.0.0",
        "publication_scope": "release_candidate",
        "external_ref_verified": True,
        "artifact_count": 8,
        "required_test_skipped": 0,
        "cleanup_residue_count": 0,
        "private_data_finding_count": 0,
        "synthetic": True,
    }


def cleanup_evidence(path, signer, release_path, release_bundle):
    payload = CleanupEvidencePayload(
        schema_version="cleanup-evidence-v1",
        target_fingerprint="1" * 64,
        ownership_verified=True,
        resources_examined=4,
        resources_removed=4,
        residue_count=0,
        synthetic=True,
    )
    bundle = EvidenceIssuer(
        signer=signer,
        clock=lambda: datetime.now(timezone.utc),
    ).issue(
        payload_type="cleanup-evidence",
        payload=payload,
        policy_result=CleanupEvidencePolicy().evaluate(payload),
        producer="tests.cleanup",
        tool_version="1.0.0",
        revision="abcdef1",
        scope="memory.cleanup.evidence",
        input_manifest=(
            input_artifact_from_bundle(
                path=release_path,
                logical_path="release-preflight-evidence",
                bundle=release_bundle,
            ),
            InputArtifact(
                path="external-cleanup-record",
                sha256="2" * 64,
                receipt_sha256="2" * 64,
                size_bytes=1,
                media_type="application/json",
            ),
        ),
    )
    AtomicEvidenceWriter().write(path, bundle)


def test_publication_policy_holds_synthetic_evidence():
    record = PublicationRecord.model_validate(publication_record())
    payload = publication.build_publication_evidence(record)

    result = PublicationEvidencePolicy().evaluate(payload)

    assert result.verification_status.value == "PASS"
    assert result.promotion_decision.value == "HOLD"


def test_cli_verifies_release_and_external_record_then_writes_signed_evidence(
    monkeypatch,
    tmp_path,
    capsys,
):
    secret = b"u" * 32
    signer = HmacReceiptSigner(key_id="publication-test", secret=secret)
    release_path = tmp_path / "release.json"
    release_bundle = release_evidence(release_path, signer)
    cleanup_path = tmp_path / "cleanup.json"
    cleanup_evidence(cleanup_path, signer, release_path, release_bundle)
    record_path = tmp_path / "publication-record.json"
    record_path.write_text(
        json.dumps(publication_record(), sort_keys=True),
        encoding="utf-8",
    )
    expected_record_sha256 = sha256_bytes(record_path.read_bytes())
    output = tmp_path / "publication-evidence.json"
    monkeypatch.setenv("EVIDENCE_REVISION", "abcdef1")
    monkeypatch.setenv("EVIDENCE_HMAC_KEY_ID", "publication-test")
    monkeypatch.setenv(
        "EVIDENCE_HMAC_SECRET_B64",
        base64.b64encode(secret).decode("ascii"),
    )

    assert publication.main(
        [
            "--release-evidence",
            str(release_path),
            "--release-revision",
            "abcdef1",
            "--cleanup-evidence",
            str(cleanup_path),
            "--cleanup-revision",
            "abcdef1",
            "--publication-record",
            str(record_path),
            "--expected-record-sha256",
            expected_record_sha256,
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
        expected_scope="memory.publication.evidence",
    )
    assert verified.bundle.artifact.payload_type == "publication-evidence"
    assert verified.bundle.artifact.verification_status.value == "PASS"
    assert verified.bundle.artifact.promotion_decision.value == "HOLD"
    assert [
        item.path for item in verified.bundle.artifact.envelope.input_manifest
    ] == [
        "release-preflight-evidence",
        "cleanup-evidence",
        "external-publication-record",
    ]
    stdout = capsys.readouterr().out
    assert "VERIFICATION_STATUS=PASS" in stdout
    assert "PROMOTION_DECISION=HOLD" in stdout


def test_cli_rejects_wrong_record_hash(monkeypatch, tmp_path):
    secret = b"u" * 32
    signer = HmacReceiptSigner(key_id="publication-test", secret=secret)
    release_path = tmp_path / "release.json"
    release_bundle = release_evidence(release_path, signer)
    cleanup_path = tmp_path / "cleanup.json"
    cleanup_evidence(cleanup_path, signer, release_path, release_bundle)
    record_path = tmp_path / "publication-record.json"
    record_path.write_text(json.dumps(publication_record()), encoding="utf-8")
    output = tmp_path / "publication-evidence.json"
    monkeypatch.setenv("EVIDENCE_REVISION", "abcdef1")
    monkeypatch.setenv("EVIDENCE_HMAC_KEY_ID", "publication-test")
    monkeypatch.setenv(
        "EVIDENCE_HMAC_SECRET_B64",
        base64.b64encode(secret).decode("ascii"),
    )

    assert publication.main(
        [
            "--release-evidence",
            str(release_path),
            "--release-revision",
            "abcdef1",
            "--cleanup-evidence",
            str(cleanup_path),
            "--cleanup-revision",
            "abcdef1",
            "--publication-record",
            str(record_path),
            "--expected-record-sha256",
            "0" * 64,
            "--output",
            str(output),
        ]
    ) == 1
    assert not output.exists()
