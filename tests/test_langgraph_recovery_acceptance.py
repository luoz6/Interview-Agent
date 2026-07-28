import json

from scripts.langgraph_recovery_acceptance import (
    CHECKS,
    build_acceptance_result,
    write_artifacts,
)


def test_acceptance_check_set_is_stable():
    assert len(CHECKS) == 10
    assert "partial_stream_reset" in CHECKS
    assert "privacy_allowlist" in CHECKS


def test_acceptance_artifacts_are_sanitized(tmp_path):
    result = build_acceptance_result(
        status="PASS",
        duration_seconds=1.25,
        test_count=10,
        commit_id="abc1234",
    )

    write_artifacts(result, tmp_path)

    serialized = json.dumps(result, ensure_ascii=False)
    assert result["status"] == "PASS"
    assert result["rpo"] == "zero_acknowledged_commands"
    for forbidden in (
        "postgresql://",
        "answer_text",
        "provider_payload",
        "checkpoint_id",
        "lease_owner",
    ):
        assert forbidden not in serialized
        assert forbidden not in (tmp_path / "result.md").read_text(
            encoding="utf-8"
        )
