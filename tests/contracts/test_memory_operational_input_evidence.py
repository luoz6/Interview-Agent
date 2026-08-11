from __future__ import annotations

import base64
import json

import pytest

from contracts.evidence import EvidenceRegistry, EvidenceVerifier, HmacReceiptSigner
from contracts.evidence.digest import sha256_bytes
from scripts import memory_operational_input_evidence as publisher
from tests.operational_shadow_fixtures import operational_input_records


@pytest.mark.parametrize("profile", tuple(publisher.PROFILES))
def test_each_operational_input_profile_writes_a_verified_bundle(
    profile,
    monkeypatch,
    tmp_path,
    capsys,
):
    secret = b"i" * 32
    signer = HmacReceiptSigner(key_id="operational-input-test", secret=secret)
    record_value = operational_input_records()[profile]
    record = tmp_path / f"{profile}-record.json"
    record_bytes = json.dumps(
        record_value,
        sort_keys=True,
    ).encode("utf-8")
    record.write_bytes(record_bytes)
    output = tmp_path / f"{profile}-evidence.json"
    monkeypatch.setenv("EVIDENCE_HMAC_KEY_ID", "operational-input-test")
    monkeypatch.setenv(
        "EVIDENCE_HMAC_SECRET_B64",
        base64.b64encode(secret).decode("ascii"),
    )

    assert publisher.main(
        [
            profile,
            "--input-record",
            str(record),
            "--expected-input-sha256",
            sha256_bytes(record_bytes),
            "--synthetic",
            "--output-revision",
            "bcdefa2",
            "--output",
            str(output),
        ]
    ) == 0

    definition = publisher.PROFILES[profile]
    verified = EvidenceVerifier(
        registry=EvidenceRegistry.default(),
        receipt_signer=signer,
    ).verify(
        json.loads(output.read_text(encoding="utf-8")),
        expected_revision="bcdefa2",
        expected_scope=definition.scope,
    )
    assert verified.bundle.artifact.payload_type == definition.payload_type
    assert verified.bundle.artifact.verification_status.value == "PASS"
    assert verified.bundle.artifact.promotion_decision.value == "HOLD"
    assert len(verified.bundle.artifact.envelope.input_manifest) == 1
    manifest = verified.bundle.artifact.envelope.input_manifest[0]
    assert manifest.path == f"external-{profile}-operational-record"
    assert str(tmp_path) not in manifest.path
    assert "VERIFICATION_STATUS=PASS" in capsys.readouterr().out


def test_publisher_rejects_wrong_hash_without_writing_output(
    monkeypatch,
    tmp_path,
    capsys,
):
    secret = b"i" * 32
    record = tmp_path / "rc-record.json"
    record.write_text(
        json.dumps(operational_input_records()["rc"]),
        encoding="utf-8",
    )
    output = tmp_path / "rc-evidence.json"
    monkeypatch.setenv("EVIDENCE_HMAC_KEY_ID", "operational-input-test")
    monkeypatch.setenv(
        "EVIDENCE_HMAC_SECRET_B64",
        base64.b64encode(secret).decode("ascii"),
    )

    assert publisher.main(
        [
            "rc",
            "--input-record",
            str(record),
            "--expected-input-sha256",
            "0" * 64,
            "--synthetic",
            "--output-revision",
            "bcdefa2",
            "--output",
            str(output),
        ]
    ) == 1

    assert not output.exists()
    stdout = capsys.readouterr().out
    assert "OPERATIONAL_INPUT_EVIDENCE=BLOCKED" in stdout
    assert "GATE=OPERATIONAL_INPUT_RECORD_UNVERIFIED" in stdout
    assert "=PASS" not in stdout


def test_publisher_rejects_coerced_boolean_and_revision_mismatch(
    monkeypatch,
    tmp_path,
    capsys,
):
    secret = b"i" * 32
    monkeypatch.setenv("EVIDENCE_HMAC_KEY_ID", "operational-input-test")
    monkeypatch.setenv(
        "EVIDENCE_HMAC_SECRET_B64",
        base64.b64encode(secret).decode("ascii"),
    )
    record_value = operational_input_records()["rc"]
    record_value["release_candidate"]["passed"] = "false"
    record = tmp_path / "rc-record.json"
    record_bytes = json.dumps(record_value).encode("utf-8")
    record.write_bytes(record_bytes)
    output = tmp_path / "rc-evidence.json"

    assert publisher.main(
        [
            "rc",
            "--input-record",
            str(record),
            "--expected-input-sha256",
            sha256_bytes(record_bytes),
            "--synthetic",
            "--output-revision",
            "abcdef1",
            "--output",
            str(output),
        ]
    ) == 1

    assert not output.exists()
    assert "GATE=OPERATIONAL_INPUT_RECORD_UNVERIFIED" in capsys.readouterr().out


def test_publisher_requires_explicit_synthetic_attestation(
    monkeypatch,
    tmp_path,
    capsys,
):
    secret = b"i" * 32
    monkeypatch.setenv("EVIDENCE_HMAC_KEY_ID", "operational-input-test")
    monkeypatch.setenv(
        "EVIDENCE_HMAC_SECRET_B64",
        base64.b64encode(secret).decode("ascii"),
    )
    record = tmp_path / "status-record.json"
    record_bytes = json.dumps(operational_input_records()["status"]).encode(
        "utf-8"
    )
    record.write_bytes(record_bytes)
    output = tmp_path / "status-evidence.json"

    assert publisher.main(
        [
            "status",
            "--input-record",
            str(record),
            "--expected-input-sha256",
            sha256_bytes(record_bytes),
            "--output-revision",
            "bcdefa2",
            "--output",
            str(output),
        ]
    ) == 1

    assert not output.exists()
    assert "GATE=OPERATIONAL_INPUT_RECORD_UNVERIFIED" in capsys.readouterr().out
