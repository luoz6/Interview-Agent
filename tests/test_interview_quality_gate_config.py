import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.services.interview_quality_gate import (
    GateConfig,
    evaluate_metric,
    gate_config_sha256,
    load_gate_config,
    main,
    render_metric_markdown,
)


CONFIG_PATH = Path("config/interview_quality_v1_gate.json")


def test_gate_config_loads_as_the_single_versioned_metric_source():
    config = load_gate_config(CONFIG_PATH)

    assert config.schema_version == "interview-quality-gate-config-v1"
    assert config.config_id == "interview-quality-v1-2026-08-05"
    assert not config.change_policy.lower_threshold_to_pass_current_run
    assert len(gate_config_sha256(CONFIG_PATH)) == 64
    assert {
        "report_scoring",
        "followup_quality",
        "initial_question_quality",
        "report_quality",
        "operations",
    } == set(config.metric_groups)


def test_old_seventy_percent_interval_hit_rate_is_blocking_fail():
    result = evaluate_metric(
        load_gate_config(CONFIG_PATH),
        "report_scoring.expected_range_attempt_hit_rate",
        actual=0.70,
        sample_size=40,
    )

    assert result.status == "FAIL"
    assert result.blocking is True
    assert result.configured_threshold == 0.90
    assert result.deviation == pytest.approx(-0.20)


def test_insufficient_sample_cannot_be_reported_as_pass():
    result = evaluate_metric(
        load_gate_config(CONFIG_PATH),
        "report_scoring.strong_attempt_hit_rate",
        actual=1.0,
        sample_size=9,
    )

    assert result.status == "INSUFFICIENT_SAMPLE"
    assert result.minimum_sample_size == 10


def test_missing_required_baseline_cannot_be_replaced_with_zero():
    config = load_gate_config(CONFIG_PATH)
    missing = evaluate_metric(
        config,
        "operations.report_completion_p95_seconds",
        actual=50,
        sample_size=30,
    )
    comparable = evaluate_metric(
        config,
        "operations.report_completion_p95_seconds",
        actual=50,
        sample_size=30,
        baseline=60,
    )

    assert missing.status == "INSUFFICIENT_BASELINE"
    assert comparable.status == "PASS"
    assert comparable.effective_threshold == 72


def test_ranking_gate_cannot_regress_below_a_stronger_frozen_baseline():
    result = evaluate_metric(
        load_gate_config(CONFIG_PATH),
        "report_scoring.pairwise_ranking_accuracy",
        actual=0.96,
        sample_size=20,
        baseline=0.98,
    )

    assert result.status == "FAIL"
    assert result.effective_threshold == 0.98


def test_unlimited_usage_is_recorded_instead_of_compared_to_a_fake_limit():
    result = evaluate_metric(
        load_gate_config(CONFIG_PATH),
        "operations.session_input_tokens",
        actual=123456,
        sample_size=1,
    )

    assert result.status == "RECORDED"
    assert result.effective_threshold is None


def test_markdown_and_cli_use_the_same_loader_and_threshold(capsys):
    config = load_gate_config(CONFIG_PATH)
    result = evaluate_metric(
        config,
        "report_scoring.expected_range_attempt_hit_rate",
        actual=0.70,
        sample_size=40,
    )

    assert "0.9" in render_metric_markdown(result)
    exit_code = main(
        [
            "--config",
            str(CONFIG_PATH),
            "--metric",
            "report_scoring.expected_range_attempt_hit_rate",
            "--actual",
            "0.70",
            "--sample-size",
            "40",
            "--format",
            "json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert payload["status"] == "FAIL"
    assert payload["effective_threshold"] == 0.90


def test_unknown_fields_and_threshold_drift_fail_closed():
    payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    payload["metric_groups"]["report_scoring"]["expected_range_attempt_hit_rate"][
        "shadow_threshold"
    ] = 0.70

    with pytest.raises(ValidationError, match="shadow_threshold"):
        GateConfig.model_validate(payload)


def test_unknown_metric_key_fails_closed():
    with pytest.raises(KeyError, match="unknown GateConfig metric"):
        evaluate_metric(
            load_gate_config(CONFIG_PATH),
            "report_scoring.not_a_real_gate",
            actual=1,
            sample_size=40,
        )
