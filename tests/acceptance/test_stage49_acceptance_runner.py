from __future__ import annotations

import base64
import json

from contracts.evidence import EvidenceRegistry, EvidenceVerifier, HmacReceiptSigner
from scripts import repository_acceptance as stage49
from scripts.repository_acceptance import (
    build_stage49_evidence,
    evaluate_stage49_acceptance,
)


def test_stage49_acceptance_reports_repository_readiness_only_when_all_pass():
    result = evaluate_stage49_acceptance(
        {"foundation": True, "privacy": True}
    )
    assert result["status"] == "READY_FOR_CONTEXT_BUDGET_CANARY"
    assert result["production_observation"] == "NOT_RUN"
    assert result["context_policy_version"] == "context-v1"


def test_stage49_acceptance_fails_any_repository_gate():
    result = evaluate_stage49_acceptance(
        {"foundation": True, "privacy": False}
    )
    assert result["status"] == "FAILED_REPOSITORY_GATE"
    assert result["production_observation"] == "NOT_RUN"


def test_stage49_payload_holds_synthetic_readiness():
    result = evaluate_stage49_acceptance(
        {"foundation": True, "release_defaults": True}
    )
    payload = build_stage49_evidence(result, synthetic=True)

    policy_result = stage49.Stage49ContextBudgetCanaryEvidencePolicy().evaluate(
        payload
    )

    assert policy_result.verification_status.value == "PASS"
    assert policy_result.promotion_decision.value == "HOLD"


def test_cli_writes_signed_synthetic_stage49_evidence(
    monkeypatch,
    tmp_path,
    capsys,
):
    secret = b"n" * 32
    output = tmp_path / "stage49-evidence.json"
    monkeypatch.setattr(stage49, "run_pytest", lambda arguments: True)
    monkeypatch.setattr(stage49, "release_defaults_are_safe", lambda: True)
    monkeypatch.setenv("EVIDENCE_REVISION", "abcdef1")
    monkeypatch.setenv("EVIDENCE_HMAC_KEY_ID", "stage49-test")
    monkeypatch.setenv(
        "EVIDENCE_HMAC_SECRET_B64",
        base64.b64encode(secret).decode("ascii"),
    )

    assert stage49.main(["stage49", "--synthetic", "--output", str(output)]) == 0

    verified = EvidenceVerifier(
        registry=EvidenceRegistry.default(),
        receipt_signer=HmacReceiptSigner(
            key_id="stage49-test",
            secret=secret,
        ),
    ).verify(
        json.loads(output.read_text(encoding="utf-8")),
        expected_revision="abcdef1",
        expected_scope="stage49.context-budget.canary",
    )
    assert verified.bundle.artifact.payload_type == (
        "stage49-context-budget-canary-evidence"
    )
    assert verified.bundle.artifact.verification_status.value == "PASS"
    assert verified.bundle.artifact.promotion_decision.value == "HOLD"
    assert "VERIFICATION_STATUS=PASS" in capsys.readouterr().out
