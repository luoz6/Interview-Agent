import base64
import json

import pytest
from pydantic import ValidationError

from contracts.evidence import EvidenceRegistry, EvidenceVerifier, RestoreDrillEvidencePayload
from contracts.policies import RestoreDrillEvidencePolicy
from scripts import memory_shadow_restore_drill as restore_drill

from scripts.memory_shadow_restore_drill import (
    PRIVATE_RESIDUE_CATEGORIES,
    build_restore_evidence,
    run_restore_drill,
)


def test_restore_drill_replays_three_old_snapshots_and_all_fault_boundaries():
    result = run_restore_drill(restore_cycles=3)

    assert result["schema_version"] == "memory-shadow-restore-drill-v1"
    assert result["backup_restore_tombstone_replay"] == "PASS"
    assert result["restore_cycles"] == 3
    assert result["fault_boundaries_exercised"] == 6
    assert result["fault_reclaims_completed"] == 6
    assert result["restored_private_data_residue"] == 0
    assert result["public_knowledge_unchanged"] is True
    assert result["provider_calls"] == 0
    assert result["production_observation"] == "NOT_RUN"
    assert set(result["residue_by_category"]) == set(PRIVATE_RESIDUE_CATEGORIES)
    assert set(result["restored_rows_by_category"]) == set(
        PRIVATE_RESIDUE_CATEGORIES
    )
    assert all(value == 0 for value in result["residue_by_category"].values())
    assert all(
        result["restored_rows_by_category"][key] > 0
        for key in PRIVATE_RESIDUE_CATEGORIES
        if key != "session_bound_consent_bindings"
    )
    assert result["restored_rows_by_category"]["session_bound_consent_bindings"] == 0
    payload = build_restore_evidence(result)
    policy_result = RestoreDrillEvidencePolicy().evaluate(payload)
    assert policy_result.verification_status.value == "PASS"
    assert policy_result.promotion_decision.value == "HOLD"


def test_restore_drill_evidence_is_aggregate_only():
    result = run_restore_drill(restore_cycles=3)
    rendered = json.dumps(result, sort_keys=True).casefold()

    for blocked in (
        "session_id",
        "principal_id",
        "fact_id",
        "normalized_fact",
        "source_manifest",
        "source_excerpt",
        "artifact_ref",
        "prompt",
        "answer",
        "resume",
        "postgresql://",
        "table_prefix",
        "database_fingerprint",
    ):
        assert blocked not in rendered


def test_restore_drill_requires_at_least_three_cycles():
    with pytest.raises(ValueError, match="at least three"):
        run_restore_drill(restore_cycles=2)


def test_strict_restore_payload_rejects_private_or_extra_fields():
    value = build_restore_evidence(run_restore_drill()).model_dump(mode="json")
    value["session_id"] = "private"

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        RestoreDrillEvidencePayload.model_validate(value)


def test_execute_writes_signed_restore_evidence(monkeypatch, tmp_path, capsys):
    secret = b"r" * 32
    output = tmp_path / "restore-evidence.json"
    monkeypatch.setenv("EVIDENCE_REVISION", "abcdef1")
    monkeypatch.setenv("EVIDENCE_HMAC_KEY_ID", "restore-test")
    monkeypatch.setenv(
        "EVIDENCE_HMAC_SECRET_B64",
        base64.b64encode(secret).decode("ascii"),
    )

    assert restore_drill.main(
        ["--execute", "--evidence-output", str(output)]
    ) == 0

    verified = EvidenceVerifier(
        registry=EvidenceRegistry.default(),
        receipt_signer=restore_drill.load_receipt_signer(
            {
                "EVIDENCE_HMAC_KEY_ID": "restore-test",
                "EVIDENCE_HMAC_SECRET_B64": base64.b64encode(secret).decode(
                    "ascii"
                ),
            }
        ),
    ).verify(
        json.loads(output.read_text(encoding="utf-8")),
        expected_revision="abcdef1",
        expected_scope="memory.shadow.restore-drill",
    )
    assert verified.bundle.artifact.payload_type == "restore-drill-evidence"
    assert verified.bundle.artifact.verification_status.value == "PASS"
    assert verified.bundle.artifact.promotion_decision.value == "HOLD"
    assert verified.bundle.artifact.envelope.input_manifest == []
    stdout = capsys.readouterr().out
    assert "VERIFICATION_STATUS=PASS" in stdout
    assert "PROMOTION_DECISION=HOLD" in stdout


def test_execute_without_signing_configuration_fails_closed(monkeypatch, tmp_path):
    for name in (
        "EVIDENCE_REVISION",
        "EVIDENCE_HMAC_KEY_ID",
        "EVIDENCE_HMAC_SECRET_B64",
    ):
        monkeypatch.delenv(name, raising=False)

    assert restore_drill.main(
        ["--execute", "--evidence-output", str(tmp_path / "evidence.json")]
    ) == 1
