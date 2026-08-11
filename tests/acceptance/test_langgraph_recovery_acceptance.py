from __future__ import annotations

import json
from types import SimpleNamespace

from scripts import langgraph_acceptance as acceptance
from scripts.langgraph_acceptance import (
    RECOVERY_CHECKS,
    build_recovery_result,
    run_recovery_acceptance,
    write_recovery_artifacts,
)


def test_acceptance_check_set_is_stable():
    assert len(RECOVERY_CHECKS) == 10
    assert "partial_stream_reset" in RECOVERY_CHECKS
    assert "privacy_allowlist" in RECOVERY_CHECKS


def test_acceptance_artifacts_are_sanitized(tmp_path):
    result = build_recovery_result(
        status="PASS",
        duration_seconds=1.25,
        test_count=10,
        commit_id="abc1234",
    )

    write_recovery_artifacts(result, tmp_path)

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


def test_recovery_missing_postgres_configuration_fails_closed(
    monkeypatch,
    tmp_path,
):
    monkeypatch.delenv("POSTGRES_DSN", raising=False)

    result = run_recovery_acceptance(timeout=10, output_dir=tmp_path)

    assert result["status"] == "FAIL"
    assert result["test_count"] == 0
    assert result["rpo"] == "unverified"


def test_recovery_zero_real_passes_cannot_report_pass(monkeypatch, tmp_path):
    monkeypatch.setenv("POSTGRES_DSN", "configured-without-connecting")

    def runner(*args, **kwargs):
        return SimpleNamespace(returncode=0, stdout="10 skipped", stderr="")

    result = run_recovery_acceptance(
        timeout=10,
        output_dir=tmp_path,
        runner=runner,
    )

    assert result["status"] == "FAIL"
    assert result["test_count"] == 0


def test_unified_cli_dispatches_recovery_profile(monkeypatch, tmp_path, capsys):
    result = build_recovery_result(
        status="PASS",
        duration_seconds=0.1,
        test_count=10,
        commit_id="abc1234",
    )
    monkeypatch.setattr(
        acceptance,
        "run_recovery_acceptance",
        lambda **kwargs: result,
    )

    assert acceptance.main(
        ["recovery", "--output-dir", str(tmp_path)]
    ) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "PASS"
