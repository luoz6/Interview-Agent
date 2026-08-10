from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import importlib
import json
from pathlib import Path
import re
import socket

import pytest

from app.services.memory_metrics import CompressionObservation


DATASET_PATH = (
    Path(__file__).resolve().parent
    / "golden"
    / "context_compression_task_aware_v1.json"
)
EXPECTED_CATEGORIES = {
    "multilingual_literals",
    "exact_duplicate",
    "near_duplicate",
    "identity_boundary",
    "old_tradeoff",
    "unresolved_boundary",
    "prompt_injection",
    "adversarial_fact_change",
    "provider_timeout",
    "invalid_json",
    "unsupported_excerpt",
    "lease_loss",
    "counterfactual_cost_latency",
    "eligibility_bypass",
}
FAILURE_CATEGORIES = {
    "provider_timeout",
    "invalid_json",
    "unsupported_excerpt",
    "lease_loss",
}
REQUIRED_AGGREGATE_METRICS = {
    "operation",
    "workflow",
    "policy_version",
    "intent_schema_version",
    "eligibility_reason",
    "route",
    "source_token_bucket",
    "target_token_bucket",
    "result_token_bucket",
    "compression_ratio_bucket",
    "estimated_input_tokens",
    "provider_input_tokens_when_available",
    "estimator_error_basis_points",
    "source_demand_token_bucket",
    "duplicate_removed_token_bucket",
    "post_dedup_demand_token_bucket",
    "mandatory_bounded_raw_token_bucket",
    "pre_dedup_required_token_bucket",
    "post_dedup_required_token_bucket",
    "business_pre_loss_required_token_bucket",
    "shadow_post_dedup_required_token_bucket",
    "business_utilization_basis_points",
    "shadow_post_dedup_utilization_basis_points",
    "selected_unit_count",
    "dropped_unit_count",
    "truncated_unit_count",
    "deduplicated_unit_count",
    "exact_recent_preserved",
    "current_answer_preserved",
    "validation_outcome",
    "fallback_outcome",
    "provider_circuit_state",
    "validation_quarantine_state",
    "failure_state_store_outcome",
    "latency_bucket",
}
FORBIDDEN_REPORT_KEY_PARTS = {
    "prompt",
    "answer",
    "resume",
    "jd",
    "evidence_body",
    "summary",
    "excerpt",
    "focus",
    "source_id",
    "artifact_ref",
    "session_id",
    "owner_key",
    "raw_error",
    "raw_response",
    "selected_fact_ids",
    "supporting_excerpts",
}


def _dataset() -> dict:
    return json.loads(DATASET_PATH.read_text(encoding="utf-8"))


def _acceptance_module():
    try:
        return importlib.import_module(
            "scripts.context_compression_shadow_acceptance"
        )
    except ModuleNotFoundError as exc:
        pytest.fail(
            "Task 9 RED: scripts.context_compression_shadow_acceptance "
            f"is not implemented: {exc}"
        )


def _case(dataset: dict, case_id: str) -> dict:
    return next(item for item in dataset["cases"] if item["case_id"] == case_id)


def _walk_keys(value):
    if isinstance(value, dict):
        for key, item in value.items():
            yield str(key)
            yield from _walk_keys(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_keys(item)


class ReplayProvider:
    """A deterministic fixture replay. It has no network or Provider client."""

    def __init__(self):
        self.calls: list[str] = []
        self.real_provider_call_count = 0

    def __call__(self, case: dict):
        fixture = case["fake_provider"]
        if not fixture.get("provider_attempted", True):
            raise AssertionError("provider must not run for this fixture")
        self.calls.append(case["case_id"])
        outcome = fixture["outcome"]
        if outcome == "timeout":
            raise TimeoutError(fixture["raw_error"])
        if outcome == "invalid_json":
            return fixture["raw_response"]
        if outcome != "success":
            raise AssertionError(f"unexpected replay outcome: {outcome}")
        return fixture["payload"]


def test_dataset_is_versioned_synthetic_and_has_exactly_sixteen_unique_cases():
    dataset = _dataset()

    assert dataset["schema_version"] == "context-compression-task-aware-v1"
    assert dataset["synthetic_only"] is True
    assert dataset["model_judge_authoritative"] is False
    assert set(dataset["required_aggregate_metrics"]) == (
        REQUIRED_AGGREGATE_METRICS
    )
    assert len(dataset["cases"]) == 16
    assert len({case["case_id"] for case in dataset["cases"]}) == 16


def test_dataset_covers_required_languages_quality_and_failure_boundaries():
    dataset = _dataset()
    categories = {case["category"] for case in dataset["cases"]}

    assert {case["language_bucket"] for case in dataset["cases"]} >= {
        "zh_hans",
        "en",
        "mixed",
    }
    assert categories == EXPECTED_CATEGORIES
    assert FAILURE_CATEGORIES <= categories
    injection = _case(dataset, "candidate-prompt-injection")
    assert {
        source["identity"]["role"] for source in injection["sources"]
    } >= {"candidate", "evidence"}
    assert {"candidate-injection", "evidence-injection"} <= set(
        injection["expected"]["excluded_fact_ids"]
    )


def test_dataset_distinguishes_equivalence_near_duplicates_and_identity():
    dataset = _dataset()
    exact = _case(dataset, "exact-evidence-duplicate")["sources"]
    near = _case(dataset, "near-duplicate-distinct-evidence")["sources"]
    identity = _case(
        dataset, "same-text-distinct-question-identity"
    )["sources"]

    assert exact[0]["text"] == exact[1]["text"]
    assert exact[0]["equivalence_key"] == exact[1]["equivalence_key"]
    assert near[0]["text"] != near[1]["text"]
    assert near[0]["equivalence_key"] != near[1]["equivalence_key"]
    assert identity[0]["text"] == identity[1]["text"]
    assert identity[0]["equivalence_key"] != identity[1]["equivalence_key"]
    assert identity[0]["identity"] != identity[1]["identity"]
    assert _case(dataset, "exact-evidence-duplicate")["expected"][
        "observation_overrides"
    ]["deduplicated_unit_count"] == 1
    assert _case(dataset, "near-duplicate-distinct-evidence")["expected"][
        "observation_overrides"
    ]["deduplicated_unit_count"] == 0
    assert _case(dataset, "same-text-distinct-question-identity")["expected"][
        "observation_overrides"
    ]["deduplicated_unit_count"] == 0


def test_every_case_resolves_a_complete_bounded_observation_contract():
    dataset = _dataset()
    defaults = dataset["observation_defaults"]

    for case in dataset["cases"]:
        observation = {
            **defaults,
            **case["expected"].get("observation_overrides", {}),
        }
        assert REQUIRED_AGGREGATE_METRICS <= set(observation), case["case_id"]
        assert observation["operation"] == case["operation"]
        assert observation["workflow"] == case["workflow"]
        assert observation["language_bucket"] == case["language_bucket"]
        assert observation["route"] == case["expected"]["route"]
        assert observation["validation_outcome"] == case["expected"][
            "validation_outcome"
        ]
        assert observation["fallback_outcome"] == case["expected"][
            "fallback_outcome"
        ]
        CompressionObservation.model_validate(observation)


@pytest.mark.parametrize(
    ("mutation", "message"),
    (
        (lambda data: data.update(synthetic_only=False), "synthetic"),
        (
            lambda data: data.update(model_judge_authoritative=True),
            "judge",
        ),
        (
            lambda data: data["cases"].append(deepcopy(data["cases"][0])),
            "duplicate",
        ),
    ),
)
def test_loader_fails_closed_on_unsafe_or_ambiguous_dataset(
    tmp_path,
    mutation,
    message,
):
    module = _acceptance_module()
    dataset = _dataset()
    mutation(dataset)
    path = tmp_path / "invalid-context-compression-dataset.json"
    path.write_text(json.dumps(dataset, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        module.load_dataset(path)


def test_evaluation_uses_only_fake_replay_and_covers_every_case(monkeypatch):
    module = _acceptance_module()
    dataset = module.load_dataset(DATASET_PATH)
    provider = ReplayProvider()

    def fail_network(*_args, **_kwargs):
        raise AssertionError("repository acceptance must not use network")

    monkeypatch.setattr(socket, "create_connection", fail_network)
    report = module.evaluate_dataset(
        dataset,
        provider,
        lambda: datetime(2026, 8, 11, tzinfo=timezone.utc),
    )

    expected_calls = [
        case["case_id"]
        for case in dataset["cases"]
        if case["expected"]["provider_attempt_count"] == 1
    ]
    assert provider.calls == expected_calls
    assert provider.real_provider_call_count == 0
    assert report["schema_version"] == (
        "context-compression-shadow-acceptance-v1"
    )
    assert report["overall_status"] == "passed"
    assert report["synthetic_case_count"] == 16
    assert report["real_provider_call_count"] == 0
    assert report["fake_provider_attempt_count"] == len(expected_calls)
    assert report["aggregates"]["passed_case_count"] == 16
    assert report["aggregates"]["failed_case_count"] == 0


def test_task_aware_quality_beats_baseline_and_judge_cannot_decide_gate():
    module = _acceptance_module()
    report = module.evaluate_dataset(
        module.load_dataset(DATASET_PATH),
        ReplayProvider(),
        lambda: datetime(2026, 8, 11, tzinfo=timezone.utc),
    )
    aggregates = report["aggregates"]

    assert aggregates["task_aware_relevance_basis_points"] > (
        aggregates["baseline_relevance_basis_points"]
    )
    assert aggregates["task_aware_preservation_basis_points"] > (
        aggregates["baseline_preservation_basis_points"]
    )
    assert aggregates["advisory_judge_disagreement_count"] > 0
    assert report["overall_status"] == "passed"


def test_failure_fixtures_are_single_attempt_deterministic_fallbacks():
    module = _acceptance_module()
    dataset = module.load_dataset(DATASET_PATH)
    report = module.evaluate_dataset(
        dataset,
        ReplayProvider(),
        lambda: datetime(2026, 8, 11, tzinfo=timezone.utc),
    )
    aggregates = report["aggregates"]

    assert aggregates["failure_category_counts"] == {
        "invalid_json": 1,
        "lease_loss": 1,
        "provider_timeout": 1,
        "unsupported_source": 1,
    }
    assert aggregates["route_counts"]["artifact_fallback"] == 5
    assert aggregates["fallback_outcome_counts"]["deterministic"] == 5
    assert aggregates["max_provider_attempt_count"] == 1
    assert aggregates["provider_retry_count"] == 0


def test_acceptance_report_contains_only_bounded_aggregate_metadata():
    module = _acceptance_module()
    dataset = module.load_dataset(DATASET_PATH)
    report = module.evaluate_dataset(
        dataset,
        ReplayProvider(),
        lambda: datetime(2026, 8, 11, tzinfo=timezone.utc),
    )
    rendered = json.dumps(report, ensure_ascii=False, sort_keys=True)

    for key in _walk_keys(report):
        lowered = key.lower()
        assert not any(part in lowered for part in FORBIDDEN_REPORT_KEY_PARTS)
    for case in dataset["cases"]:
        for marker in case["expected"].get("forbidden_output_markers", []):
            assert marker not in rendered
        for marker in case["expected"].get("forbidden_report_markers", []):
            assert marker not in rendered
    assert re.search(r"(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])", rendered) is None
    assert set(report) == {
        "schema_version",
        "dataset_schema_version",
        "overall_status",
        "synthetic_case_count",
        "real_provider_call_count",
        "fake_provider_attempt_count",
        "aggregates",
        "observations",
    }


def test_run_acceptance_loads_the_declared_dataset_without_real_provider():
    module = _acceptance_module()
    provider = ReplayProvider()

    report = module.run_acceptance(
        DATASET_PATH,
        provider,
        lambda: datetime(2026, 8, 11, tzinfo=timezone.utc),
    )

    assert report["overall_status"] == "passed"
    assert report["dataset_schema_version"] == (
        "context-compression-task-aware-v1"
    )
    assert report["real_provider_call_count"] == 0
