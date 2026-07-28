import json

import argparse
import pytest

from scripts.audit_agent_runtime import audit_runtime_control_payloads
from scripts.langgraph_canary import parse_utc_timestamp, write_canary_artifacts
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
    assert "Phase: joint" in markdown
    assert "interview_assigned_count: 10" in markdown
    assert "projection_divergence_count: 0" in markdown


def test_canary_artifacts_refuse_to_overwrite_a_phase(tmp_path):
    result = snapshot()

    write_canary_artifacts(result, tmp_path)
    with pytest.raises(FileExistsError, match="already exist"):
        write_canary_artifacts(result, tmp_path)


def test_phase_start_requires_explicit_utc():
    assert parse_utc_timestamp("2026-07-25T10:00:00Z").utcoffset().total_seconds() == 0
    assert parse_utc_timestamp("2026-07-25T10:00:00+00:00").utcoffset().total_seconds() == 0
    for invalid in (
        "2026-07-25T10:00:00",
        "2026-07-25T10:00:00+08:00",
        "not-a-time",
    ):
        with pytest.raises(argparse.ArgumentTypeError):
            parse_utc_timestamp(invalid)


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
