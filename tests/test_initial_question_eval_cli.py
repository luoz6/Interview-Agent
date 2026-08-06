from __future__ import annotations

import json

import pytest

import scripts.evaluate_initial_question_quality as cli
from app.services.followup_provider_preflight import (
    DeepSeekDiscoverySnapshot,
    ProviderPrice,
)


def test_fixture_cli_writes_full_evidence_and_returns_blocked_not_pass(tmp_path):
    code = cli.main(
        [
            "--mode", "fixture-replay",
            "--scope", "full",
            "--purpose", "evaluation",
            "--partition", "all",
            "--out", str(tmp_path),
            "--run-id", "fixture-run",
        ]
    )
    manifest = json.loads(
        (tmp_path / "fixture-run" / "manifest.json").read_text(encoding="utf-8")
    )
    metrics = json.loads(
        (tmp_path / "fixture-run" / "metrics.json").read_text(encoding="utf-8")
    )

    assert code == 2
    assert manifest["provider_called"] is False
    assert manifest["provider_invocations_this_run"] == 0
    assert manifest["decision"] == "BLOCKED_SYNTHETIC_FIXTURE_ONLY"
    assert metrics["automated_status"] == "PASS"
    assert metrics["attempt_count"] == 24
    assert metrics["provider_usage"]["recorded_source_invocations"] == 0


def test_development_cli_cannot_consume_blind_test(tmp_path):
    with pytest.raises(SystemExit, match="cannot consume blind-test"):
        cli.main(
            [
                "--purpose", "development",
                "--partition", "all",
                "--out", str(tmp_path),
            ]
        )


def test_provider_cli_stops_before_data_request_on_model_drift(
    monkeypatch, tmp_path
):
    price = ProviderPrice(
        cache_hit_input_per_million=0.1,
        cache_miss_input_per_million=0.2,
        output_per_million=0.3,
    )
    monkeypatch.setenv("DEEPSEEK_API_KEY", "secret-not-written")
    monkeypatch.setattr(
        cli,
        "discover_deepseek_provider",
        lambda **_kwargs: DeepSeekDiscoverySnapshot(
            observed_at="2026-08-06T00:00:00Z",
            models_endpoint_ok=True,
            model_ids=["deepseek-v4-flash", "deepseek-v4-pro"],
            pricing_page_ok=True,
            prices={"deepseek-v4-pro": price, "deepseek-v4-flash": price},
        ),
    )

    code = cli.main(
        [
            "--mode", "provider",
            "--scope", "smoke",
            "--purpose", "evaluation",
            "--partition", "all",
            "--out", str(tmp_path),
            "--run-id", "provider-drift",
        ]
    )
    manifest_path = tmp_path / "provider-drift" / "manifest.json"
    text = manifest_path.read_text(encoding="utf-8")
    manifest = json.loads(text)

    assert code == 2
    assert manifest["provider_called"] is False
    assert manifest["first_data_request_sent"] is False
    assert manifest["hard_stop_conditions"] == ["MODEL_VERSION_DRIFT"]
    assert "secret-not-written" not in text


def test_nonempty_run_directory_is_rejected(tmp_path):
    run_dir = tmp_path / "existing"
    run_dir.mkdir()
    (run_dir / "stale.json").write_text("{}", encoding="utf-8")

    with pytest.raises(SystemExit, match="already exists"):
        cli.main(["--out", str(tmp_path), "--run-id", "existing"])
