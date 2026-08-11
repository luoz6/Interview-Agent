from __future__ import annotations

import base64
from datetime import datetime, timezone
import json

import pytest

from contracts.evidence import (
    AtomicEvidenceWriter,
    EvidenceIssuer,
    HmacReceiptSigner,
)
from contracts.policies import OperationalRcEvidencePolicy

from scripts.memory_validation_foundation_acceptance import (
    AcceptanceBlocked,
    SUCCESS_LINES,
    main,
    run_acceptance,
)
from tests.operational_shadow_fixtures import rc_payload


def test_success_output_is_exact_and_keeps_consumption_blocked():
    assert run_acceptance(rc_payload()) == SUCCESS_LINES
    assert "PASS_FOR_PRODUCTION" not in "\n".join(SUCCESS_LINES)


def test_missing_operational_evidence_blocks_ready():
    payload = rc_payload().model_copy(
        update={"browser_passed": False, "browser_failed": 1}
    )
    with pytest.raises(AcceptanceBlocked) as captured:
        run_acceptance(payload)
    assert "RC_BROWSER_NOT_GREEN" in captured.value.codes
    assert "RC_REQUIRED_GATE_FAILED" in captured.value.codes


def test_cli_prints_only_exact_success_lines(monkeypatch, tmp_path, capsys):
    secret = b"f" * 32
    signer = HmacReceiptSigner(key_id="foundation-test", secret=secret)
    payload = rc_payload()
    bundle = EvidenceIssuer(
        signer=signer,
        clock=lambda: datetime.now(timezone.utc),
    ).issue(
        payload_type="operational-rc-evidence",
        payload=payload,
        policy_result=OperationalRcEvidencePolicy().evaluate(payload),
        producer="tests.foundation-rc",
        tool_version="1.0.0",
        revision="bcdefa2",
        scope="memory.operational-rc.controlled",
    )
    path = tmp_path / "evidence.json"
    AtomicEvidenceWriter().write(path, bundle)
    monkeypatch.setenv("EVIDENCE_HMAC_KEY_ID", "foundation-test")
    monkeypatch.setenv(
        "EVIDENCE_HMAC_SECRET_B64",
        base64.b64encode(secret).decode("ascii"),
    )
    assert main(
        [
            "--evidence",
            str(path),
            "--evidence-revision",
            "bcdefa2",
        ]
    ) == 0
    assert capsys.readouterr().out.strip().splitlines() == list(SUCCESS_LINES)
