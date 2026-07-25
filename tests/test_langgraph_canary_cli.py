import json

from scripts.audit_agent_runtime import audit_runtime_control_payloads
from scripts.langgraph_canary import write_canary_artifacts
from tests.test_langgraph_canary_status import snapshot


def test_canary_artifacts_are_allowlisted_and_sanitized(tmp_path):
    result = snapshot(
        recommendation="HOLD",
        reasons=["insufficient_sample_size"],
    )

    write_canary_artifacts(result, tmp_path)

    json_payload = json.loads(
        (tmp_path / "result.json").read_text(encoding="utf-8")
    )
    markdown = (tmp_path / "result.md").read_text(encoding="utf-8")
    assert audit_runtime_control_payloads([json_payload])["status"] == "PASS"
    for forbidden in (
        "private answer",
        "provider response",
        "postgresql://",
        "checkpoint_id",
        "lease_token",
    ):
        assert forbidden not in json.dumps(json_payload)
        assert forbidden not in markdown


def test_control_audit_rejects_adversarial_canary_source_fields():
    result = audit_runtime_control_payloads(
        [
            {
                "lease_token": "private-token",
                "messages": ["private answer"],
                "provider_payload": "raw provider response",
            }
        ]
    )

    assert result["status"] == "FAIL"
    assert "$[0].lease_token" in result["privacy_violations"]
    assert "$[0].messages" in result["privacy_violations"]
    assert "$[0].provider_payload" in result["privacy_violations"]
