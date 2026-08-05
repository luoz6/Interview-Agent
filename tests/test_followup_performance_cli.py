import json
import hashlib

import pytest

from app.services.followup_performance import (
    PerformancePricingSnapshot,
    build_synthetic_performance_artifact,
)
from scripts.evaluate_followup_performance import main


def test_fixture_cli_writes_reproducible_blocked_quality_artifacts(tmp_path, capsys):
    assert main(["--out", str(tmp_path), "--run-id", "fixture-run"]) == 0

    console = json.loads(capsys.readouterr().out)
    run_dir = tmp_path / "fixture-run"
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    metrics = json.loads((run_dir / "metrics.json").read_text(encoding="utf-8"))
    artifact = json.loads(
        (run_dir / "performance-artifact.json").read_text(encoding="utf-8")
    )

    assert console["run_dir"] == str(run_dir)
    assert manifest["task"] == "T37"
    assert manifest["provider_called"] is False
    assert manifest["first_data_request_sent"] is False
    assert manifest["gate_config_sha256"]
    assert manifest["authorization_sha256"]
    assert metrics["engineering_status"] == "PASS"
    assert metrics["quality_status"] == "BLOCKED_NOT_RUN_REAL_PROVIDER"
    assert artifact["source_kind"] == "synthetic_fixture"
    assert "fixed_v1 Decision baseline: `None`" in (
        run_dir / "report.md"
    ).read_text(encoding="utf-8")


def test_cli_refuses_to_mix_an_existing_nonempty_run(tmp_path):
    run_dir = tmp_path / "existing"
    run_dir.mkdir()
    (run_dir / "old.json").write_text("{}", encoding="utf-8")

    with pytest.raises(SystemExit, match="run directory already exists"):
        main(["--out", str(tmp_path), "--run-id", "existing"])


def test_saved_replay_rejects_synthetic_artifact(tmp_path):
    responses = tmp_path / "responses.json"
    responses.write_text(
        build_synthetic_performance_artifact().model_dump_json(indent=2),
        encoding="utf-8",
    )

    with pytest.raises(SystemExit, match="requires real saved or live Provider"):
        main(
            [
                "--mode",
                "saved-replay",
                "--responses",
                str(responses),
                "--out",
                str(tmp_path),
                "--run-id",
                "synthetic-saved",
            ]
        )


def test_saved_replay_persists_model_drift_before_evaluation(tmp_path, capsys):
    synthetic = build_synthetic_performance_artifact()
    source_capture = tmp_path / "redacted-provider-capture.json"
    source_capture.write_text('{"capture":"redacted"}', encoding="utf-8")
    source_capture_sha256 = hashlib.sha256(source_capture.read_bytes()).hexdigest()
    pricing = PerformancePricingSnapshot(
        source_url="https://api-docs.deepseek.com/quick_start/pricing",
        observed_at="2026-08-05T00:00:00Z",
        cache_hit_input_per_million=0.5,
        cache_miss_input_per_million=1.0,
        output_per_million=2.0,
    )
    samples = [
        sample.model_copy(
            update={
                "source_kind": "saved_provider_replay",
                "provider_name": "DeepSeek",
                "model_id": "different-model",
                "provider_request_trace_ids": [
                    f"trace-{sample.sample_id}-{index}"
                    for index in range(sample.actual_provider_requests)
                ],
                "estimated_cost": pricing.estimate(
                    input_tokens=sample.input_tokens,
                    output_tokens=sample.output_tokens,
                    cached_input_tokens=sample.cached_input_tokens,
                ),
            }
        )
        for sample in synthetic.samples
    ]
    artifact = synthetic.model_copy(
        update={
            "source_kind": "saved_provider_replay",
            "provider_name": "DeepSeek",
            "model_id": "different-model",
            "capture_run_id": "provider-capture-drift",
            "source_capture_sha256": source_capture_sha256,
            "pricing_snapshot": pricing,
            "samples": samples,
        }
    )
    responses = tmp_path / "real-responses.json"
    responses.write_text(artifact.model_dump_json(indent=2), encoding="utf-8")

    exit_code = main(
        [
            "--mode",
            "saved-replay",
            "--responses",
            str(responses),
            "--source-capture",
            str(source_capture),
            "--out",
            str(tmp_path),
            "--run-id",
            "drift-run",
        ]
    )

    assert exit_code == 2
    assert json.loads(capsys.readouterr().out)["hard_stop_conditions"] == [
        "MODEL_VERSION_DRIFT"
    ]
    manifest = json.loads(
        (tmp_path / "drift-run" / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["provider_called"] is False
    assert manifest["first_data_request_sent"] is False
    assert manifest["decision"] == "BLOCKED_MODEL_VERSION_DRIFT"
    assert not (tmp_path / "drift-run" / "performance-artifact.json").exists()


def test_cli_requires_responses_only_for_saved_mode(tmp_path):
    with pytest.raises(SystemExit, match="--responses is required"):
        main(
            [
                "--mode",
                "saved-replay",
                "--out",
                str(tmp_path),
                "--run-id",
                "missing",
            ]
        )
