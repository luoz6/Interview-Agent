import hashlib
from collections import Counter
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.services.followup_eval import (
    FollowupEvalAttempt,
    SavedFollowupProviderArtifact,
    build_synthetic_fixture_replay,
    calculate_followup_metrics,
    fixed_policy_attempts,
    replay_saved_provider_artifact,
)
from app.services.interview_quality_dataset import load_interview_quality_dataset
from app.services.interview_quality_gate import load_gate_config


DATASET_PATH = Path(
    "tests/golden/interview_quality_v1/followup-decision-quality-v2.json"
)


def dataset_and_replay():
    dataset = load_interview_quality_dataset(DATASET_PATH)
    dataset_sha256 = hashlib.sha256(DATASET_PATH.read_bytes()).hexdigest()
    artifact = build_synthetic_fixture_replay(
        dataset,
        dataset_sha256=dataset_sha256,
    )
    adaptive = replay_saved_provider_artifact(
        dataset,
        artifact,
        dataset_sha256=dataset_sha256,
    )
    return dataset, artifact, adaptive


def test_full_fixture_replay_passes_automated_gates_but_not_review():
    dataset, artifact, adaptive = dataset_and_replay()
    metrics = calculate_followup_metrics(
        dataset,
        [*fixed_policy_attempts(dataset), *adaptive],
        gate_config=load_gate_config(),
    )

    assert artifact.source == "synthetic_fixture"
    assert metrics["dataset_case_count"] == 100
    assert metrics["automated_status"] == "PASS"
    assert metrics["quality_status"] == "BLOCKED_PENDING_INDEPENDENT_REVIEW"
    assert metrics["independent_review_status"] == "PENDING"
    assert metrics["pending_independent_review_case_count"] == 100
    assert metrics["adaptive_action_accuracy"] == 1.0
    assert metrics["fixed_action_accuracy"] < metrics["adaptive_action_accuracy"]
    assert all(
        item["status"] == "PASS" for item in metrics["metric_evaluations"]
    )
    parse_rate = next(
        item
        for item in metrics["metric_evaluations"]
        if item["metric_key"] == "followup_quality.decision_parse_rate"
    )
    assert parse_rate["actual"] == 0.98
    assert parse_rate["status"] == "PASS"
    assert len(metrics["parse_failures"]) == 2
    assert metrics["partition_action_comparison"]["blind-test"]["case_count"] == 30


def test_sequence_replay_proves_zero_to_two_limit_and_terminal_zero_calls():
    dataset, _, adaptive = dataset_and_replay()
    metrics = calculate_followup_metrics(
        dataset,
        [*fixed_policy_attempts(dataset), *adaptive],
        gate_config=load_gate_config(),
    )
    sequence = metrics["sequence_replay"]

    assert sequence["sequence_count"] == 20
    assert sequence["effective_correction_count"] == 20
    assert sequence["terminal_zero_call_checks"] == 10
    assert sequence["terminal_zero_call_passes"] == 10
    assert all(item["final_followup_count"] <= 2 for item in sequence["results"])


def test_duplicate_generation_is_rejected_and_never_counted_as_user_visible():
    dataset, _, adaptive = dataset_and_replay()
    metrics = calculate_followup_metrics(
        dataset,
        [*fixed_policy_attempts(dataset), *adaptive],
        gate_config=load_gate_config(),
    )

    assert metrics["rejected_generation_count"] == 2
    assert len(metrics["raw_duplicate_rejection_case_ids"]) == 2
    assert metrics["user_visible_duplicate_count"] == 0
    for case_id in metrics["raw_duplicate_rejection_case_ids"]:
        attempt = next(item for item in adaptive if item.case_id == case_id)
        assert attempt.generated_question is not None
        assert attempt.displayed_question is None
        assert attempt.runtime_action == "next_question"
        assert attempt.generation_rejection_reason == "duplicate_question"
        assert attempt.generation_provider_invocations == 3


def test_user_visible_repetition_remains_a_blocking_quality_failure():
    dataset, _, adaptive = dataset_and_replay()
    targets = [item for item in adaptive if item.displayed_question][:2]
    for target in targets:
        case = next(case for case in dataset.cases if case.case_id == target.case_id)
        adaptive[adaptive.index(target)] = target.model_copy(
            update={
                "generated_question": case.input["question_text"],
                "displayed_question": case.input["question_text"],
            }
        )

    metrics = calculate_followup_metrics(
        dataset,
        [*fixed_policy_attempts(dataset), *adaptive],
        gate_config=load_gate_config(),
    )
    repeat = next(
        item
        for item in metrics["metric_evaluations"]
        if item["metric_key"] == "followup_quality.repeat_original_question_rate"
    )

    assert repeat["status"] == "FAIL"
    assert metrics["automated_status"] == "FAIL"
    assert metrics["quality_status"] == "FAIL_AUTOMATED"


def test_saved_replay_rejects_dataset_hash_drift_and_incomplete_coverage():
    dataset, artifact, _ = dataset_and_replay()
    dataset_sha256 = hashlib.sha256(DATASET_PATH.read_bytes()).hexdigest()

    with pytest.raises(ValueError, match="dataset hash mismatch"):
        replay_saved_provider_artifact(
            dataset,
            artifact,
            dataset_sha256="0" * 64,
        )
    incomplete = artifact.model_copy(update={"cases": artifact.cases[:-1]})
    with pytest.raises(ValueError, match="cover the selected dataset exactly"):
        replay_saved_provider_artifact(
            dataset,
            incomplete,
            dataset_sha256=dataset_sha256,
        )


def test_real_saved_output_requires_provider_identity():
    with pytest.raises(ValidationError, match="Provider and model identity"):
        SavedFollowupProviderArtifact(
            source="local_redacted_provider_output",
            dataset_id="followup-decision-quality-v2",
            dataset_sha256="a" * 64,
            cases=[],
        )


def test_complete_real_saved_output_requires_usage_and_matching_model():
    dataset, fixture, _ = dataset_and_replay()
    case = fixture.cases[0]
    real = {
        "source": "local_redacted_provider_output",
        "dataset_id": dataset.dataset_id,
        "dataset_sha256": fixture.dataset_sha256,
        "provider_name": "DeepSeek",
        "model_id": "deepseek-chat",
        "cases": [case.model_dump(mode="json")],
    }

    with pytest.raises(ValidationError, match="per-request metering"):
        SavedFollowupProviderArtifact.model_validate(real)

    first_attempt = real["cases"][0]["decision_attempts"][0]
    first_attempt.update(
        {
            "input_tokens": 10,
            "output_tokens": 2,
            "cached_input_tokens": 0,
            "provider_model": "deepseek-v4-pro",
        }
    )
    with pytest.raises(ValidationError, match="model metadata"):
        SavedFollowupProviderArtifact.model_validate(real)

    first_attempt["provider_model"] = "deepseek-chat"
    with pytest.raises(ValidationError, match="per-request latency"):
        SavedFollowupProviderArtifact.model_validate(real)


def test_hard_stopped_capture_cannot_be_replayed_as_complete():
    dataset, fixture, _ = dataset_and_replay()
    stopped = fixture.model_copy(
        update={
            "capture_status": "hard_stopped",
            "hard_stop_conditions": ["USAGE_METERING_UNAVAILABLE"],
        }
    )

    with pytest.raises(ValueError, match="cannot be replayed as complete"):
        replay_saved_provider_artifact(
            dataset,
            stopped,
            dataset_sha256=fixture.dataset_sha256,
        )


def test_attempt_contract_rejects_inconsistent_calls_and_unsafe_display():
    base = {
        "case_id": "case-1",
        "partition": "train",
        "policy_version": "adaptive_v1",
        "parsed": True,
        "expected_action": "next_question",
        "acceptable_actions": ["next_question"],
        "predicted_action": "next_question",
        "runtime_action": "next_question",
        "followup_count_before": 0,
        "followup_count_after": 0,
    }
    with pytest.raises(ValidationError, match=r"Decision \+ Generation"):
        FollowupEvalAttempt(**base, provider_invocations=1)
    with pytest.raises(ValidationError, match="runtime_action=follow_up"):
        FollowupEvalAttempt(**base, displayed_question="unsafe")


def test_provider_failure_fixtures_replay_through_real_service_retry_logic():
    _, _, adaptive = dataset_and_replay()
    error_counts = Counter(
        item.error_code for item in adaptive if item.error_code is not None
    )

    assert error_counts == {
        "duplicate_question": 2,
        "provider_timeout": 2,
        "provider_invalid_output": 2,
        "provider_failed": 2,
    }
    for item in adaptive:
        if item.error_code in {
            "provider_timeout",
            "provider_invalid_output",
            "provider_failed",
        }:
            assert item.decision_provider_invocations == 2
            assert item.provider_retries == 1
            assert item.runtime_action == "next_question"
        assert item.replay_action == item.predicted_action
        assert item.replay_provider_invocations == 0
