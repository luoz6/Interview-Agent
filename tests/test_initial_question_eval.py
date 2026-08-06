from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.services.initial_question_eval import (
    InitialQuestionEvalAttempt,
    InitialQuestionProviderArtifact,
    InitialQuestionReview,
    build_synthetic_initial_question_attempts,
    calculate_initial_question_metrics,
    fixture_artifact,
    load_initial_question_provider_artifact,
    saved_replay_attempts,
)
from app.services.interview_plan_revision import plan_payload_sha256
from app.services.interview_quality_dataset import load_interview_quality_dataset
from app.services.interview_quality_gate import load_gate_config


DATASET_PATH = Path(
    "tests/golden/interview_quality_v1/initial-question-quality-v2.json"
)


@pytest.fixture(scope="module")
def dataset():
    return load_interview_quality_dataset(DATASET_PATH)


@pytest.fixture(scope="module")
def attempts(dataset):
    return build_synthetic_initial_question_attempts(dataset)


def test_full_fixture_replay_passes_engineering_gates_without_claiming_provider_quality(
    dataset, attempts
):
    metrics = calculate_initial_question_metrics(
        dataset, attempts, gate_config=load_gate_config()
    )

    assert len(attempts) == 24
    assert metrics["question_count"] == 144
    assert metrics["automated_status"] == "PASS"
    assert metrics["quality_status"] == "BLOCKED_SYNTHETIC_FIXTURE_ONLY"
    assert metrics["plan_budget_gate"] == {
        "version": "interview-plan-duration-budget-v1",
        "status": "PASS",
        "exact_pass_count": 24,
        "question_count_match_rate": 1.0,
        "estimated_duration_fit_rate": 1.0,
        "warning_counts": {},
        "blocking_counts": {},
    }
    assert metrics["context_and_grounding_gate"]["status"] == "PASS"
    assert metrics["semantic_checks"]["case_quality_stability_rate"] == 1.0
    assert metrics["provider_usage"]["provider_invocations_this_run"] == 0


def test_english_regression_cases_generate_english_questions(dataset, attempts):
    english_ids = {case.case_id for case in dataset.cases if case.language == "en"}
    english_attempts = [item for item in attempts if item.case_id in english_ids]

    assert len(english_attempts) == 4
    assert all(
        all(question.question_text.isascii() for question in item.plan.questions)
        for item in english_attempts
    )


def test_attempt_plan_and_session_hashes_are_fail_closed(attempts):
    payload = attempts[0].model_dump(mode="json")
    payload["plan_sha256"] = "0" * 64

    with pytest.raises(ValidationError, match="plan_sha256"):
        InitialQuestionEvalAttempt.model_validate(payload)


def test_every_case_requires_both_frozen_runs(dataset, attempts):
    with pytest.raises(ValueError, match="coverage mismatch"):
        calculate_initial_question_metrics(
            dataset, attempts[:-1], gate_config=load_gate_config()
        )


def test_budget_warning_is_a_blocking_generation_gate(dataset, attempts):
    first = attempts[0]
    changed_questions = tuple(
        question.model_copy(
            update={"expected_minutes": question.expected_minutes + 20}
        )
        for question in first.plan.questions
    )
    changed_plan = first.plan.model_copy(update={"questions": changed_questions})
    changed_hash = plan_payload_sha256(changed_plan)
    changed = first.model_copy(
        update={
            "plan": changed_plan,
            "plan_sha256": changed_hash,
            "session_snapshot_sha256": changed_hash,
        }
    )
    metrics = calculate_initial_question_metrics(
        dataset, [changed, *attempts[1:]], gate_config=load_gate_config()
    )

    assert metrics["plan_budget_gate"]["status"] == "FAIL"
    assert metrics["automated_status"] == "FAIL"
    assert "plan_budget_not_exact" in metrics["deterministic_failures"]


def test_pending_semantic_review_cannot_shrink_denominator_into_pass(dataset, attempts):
    first = attempts[0]
    pending = tuple(
        InitialQuestionReview(
            question_id=question.question_id,
            review_status="pending",
            reviewer_kind="unassigned",
        )
        for question in first.plan.questions
    )
    metrics = calculate_initial_question_metrics(
        dataset,
        [first.model_copy(update={"reviews": pending}), *attempts[1:]],
        gate_config=load_gate_config(),
    )

    semantic = [
        item
        for item in metrics["metric_evaluations"]
        if item["metric_key"]
        != "initial_question_quality.preview_session_plan_hash_match_rate"
    ]
    assert all(item["sample_size"] == 0 for item in semantic)
    assert all(item["status"] == "INSUFFICIENT_SAMPLE" for item in semantic)
    assert metrics["quality_status"] == "BLOCKED_PENDING_INDEPENDENT_REVIEW"


def test_context_or_grounding_truncation_is_a_deterministic_failure(dataset, attempts):
    first = attempts[0]
    context = first.context_budget.model_copy(
        update={"retained_knowledge_candidate_count": 0}
    )
    metrics = calculate_initial_question_metrics(
        dataset,
        [first.model_copy(update={"context_budget": context}), *attempts[1:]],
        gate_config=load_gate_config(),
    )

    assert metrics["context_and_grounding_gate"]["status"] == "FAIL"
    assert "grounding_context_not_retained" in metrics["deterministic_failures"]


def test_semantic_duplicate_and_leak_are_not_hidden(dataset, attempts):
    first = attempts[0]
    reviews = list(first.reviews)
    reviews[0] = reviews[0].model_copy(
        update={
            "within_plan_duplicate": True,
            "reference_or_internal_evidence_leak": True,
        }
    )
    metrics = calculate_initial_question_metrics(
        dataset,
        [first.model_copy(update={"reviews": tuple(reviews)}), *attempts[1:]],
        gate_config=load_gate_config(),
    )

    by_key = {item["metric_key"]: item for item in metrics["metric_evaluations"]}
    assert by_key[
        "initial_question_quality.reference_or_internal_evidence_leak_count"
    ]["status"] == "FAIL"
    assert metrics["automated_status"] == "FAIL"


def test_live_attempt_requires_every_invocation_to_be_metered(attempts):
    payload = attempts[0].model_dump(mode="json")
    payload.update(
        {
            "execution_source": "live_provider",
            "provider_name": "DeepSeek",
            "provider_model": "deepseek-chat",
            "provider_invocations": 2,
            "provider_metered_invocations": 1,
            "input_tokens": 20,
            "output_tokens": 5,
            "latency_seconds": 0.2,
            "response_sha256": payload["plan_sha256"],
        }
    )

    with pytest.raises(ValidationError, match="every live Provider invocation"):
        InitialQuestionEvalAttempt.model_validate(payload)


def test_saved_artifact_binds_exact_dataset_hash(dataset, tmp_path):
    artifact = fixture_artifact(
        dataset,
        dataset_sha256="0" * 64,
    )
    path = tmp_path / "replay.json"
    path.write_text(artifact.model_dump_json(), encoding="utf-8")

    with pytest.raises(ValueError, match="dataset hash mismatch"):
        load_initial_question_provider_artifact(
            path, dataset=dataset, dataset_path=DATASET_PATH
        )


def test_hard_stopped_capture_cannot_be_replayed_as_complete(dataset):
    artifact = InitialQuestionProviderArtifact(
        source="local_redacted_provider_output",
        dataset_id="initial-question-quality-v2",
        dataset_sha256="0" * 64,
        provider_name="DeepSeek",
        model_id="deepseek-chat",
        capture_status="hard_stopped",
        hard_stop_conditions=("USAGE_METERING_UNAVAILABLE",),
        outbound_requests_attempted=1,
        outbound_requests_metered=0,
        attempts=(),
    )

    with pytest.raises(ValueError, match="cannot be replayed"):
        saved_replay_attempts(artifact)


def test_fixture_artifact_does_not_claim_provider_identity_or_calls(dataset):
    digest = __import__("hashlib").sha256(DATASET_PATH.read_bytes()).hexdigest()
    artifact = fixture_artifact(dataset, dataset_sha256=digest)

    assert artifact.source == "synthetic_fixture"
    assert artifact.provider_name is None
    assert artifact.model_id is None
    assert all(item.provider_invocations == 0 for item in artifact.attempts)
