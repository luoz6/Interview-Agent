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
    main,
    run_acceptance,
)
from tests.operational_shadow_fixtures import rc_payload


LOCAL_V1_PRODUCTION_GATES = (
    "long_term_default_not_disabled",
    "long_term_shadow_default_enabled",
    "trusted_local_principal_api_default_enabled",
)


def test_local_v1_defaults_keep_production_foundation_blocked():
    with pytest.raises(AcceptanceBlocked) as captured:
        run_acceptance(rc_payload())

    assert captured.value.codes == LOCAL_V1_PRODUCTION_GATES


def test_missing_operational_evidence_blocks_ready():
    payload = rc_payload().model_copy(
        update={"browser_passed": False, "browser_failed": 1}
    )
    with pytest.raises(AcceptanceBlocked) as captured:
        run_acceptance(payload)
    assert "RC_BROWSER_NOT_GREEN" in captured.value.codes
    assert "RC_REQUIRED_GATE_FAILED" in captured.value.codes


def test_cli_prints_exact_local_v1_production_block(monkeypatch, tmp_path, capsys):
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
    ) == 1
    assert capsys.readouterr().out.strip().splitlines() == [
        "MEMORY_VALIDATION_FOUNDATION=BLOCKED",
        *(f"GATE={code}" for code in LOCAL_V1_PRODUCTION_GATES),
        "LONG_TERM_MEMORY_CONSUMPTION=BLOCKED",
        "PRODUCTION_OBSERVATION=NOT_RUN",
    ]
