from __future__ import annotations

from collections import Counter
from datetime import datetime
import json
from pathlib import Path
import re
from typing import Any, Callable, Mapping


DATASET_SCHEMA_VERSION = "context-compression-task-aware-v1"
REPORT_SCHEMA_VERSION = "context-compression-shadow-acceptance-v1"

REQUIRED_AGGREGATE_METRICS = frozenset(
    {
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
)

EXPECTED_CATEGORIES = frozenset(
    {
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
)
FAILURE_CATEGORIES = frozenset(
    {"provider_timeout", "invalid_json", "unsupported_excerpt", "lease_loss"}
)
SAFE_FAILURE_CATEGORY = {
    "provider_timeout": "provider_timeout",
    "invalid_json": "invalid_json",
    "unsupported_excerpt": "unsupported_source",
    "lease_loss": "lease_loss",
}

TOKEN_BUCKETS = frozenset(
    {
        "unknown",
        "0",
        "1_256",
        "257_512",
        "513_1024",
        "1025_2048",
        "2049_4096",
        "4097_8192",
        "8193_16384",
        "16385_32768",
        "32769_plus",
    }
)
RATIO_BUCKETS = frozenset(
    {
        "unknown",
        "0_2500_bp",
        "2501_5000_bp",
        "5001_7500_bp",
        "7501_10000_bp",
        "10001_plus_bp",
    }
)
LATENCY_BUCKETS = frozenset(
    {
        "unknown",
        "0_99_ms",
        "100_499_ms",
        "500_999_ms",
        "1000_2499_ms",
        "2500_4999_ms",
        "5000_9999_ms",
        "10000_plus_ms",
    }
)

STRING_DIMENSIONS = {
    "measurement_path": frozenset({"business", "counterfactual"}),
    "operation": frozenset(
        {
            "question_conversation",
            "evidence_compression",
            "prep_context",
            "review_context",
        }
    ),
    "workflow": frozenset({"interview", "review", "prep"}),
    "eligibility_reason": frozenset(
        {
            "none",
            "below_threshold",
            "approaching_operation_budget",
            "older_complete_turn_would_drop",
            "older_complete_turn_excessively_truncated",
            "unresolved_topic_coverage_loss",
            "evidence_representation_excessive_truncation",
            "prep_section_coverage_loss",
            "review_continuity_would_drop",
        }
    ),
    "route": frozenset(
        {
            "artifact_created",
            "artifact_reused",
            "artifact_fallback",
            "compression_bypassed",
            "deterministic",
            "memory_index_retrieved",
            "memory_index_empty",
        }
    ),
    "validation_outcome": frozenset(
        {
            "not_run",
            "valid",
            "invalid_json",
            "invalid_schema",
            "grounding_failed",
            "unsupported_excerpt",
            "numeric_literal_changed",
            "lease_lost",
            "unavailable",
        }
    ),
    "fallback_outcome": frozenset(
        {
            "not_used",
            "deterministic",
            "provider_failure",
            "validation_failure",
            "lease_loss",
            "circuit_blocked",
            "quarantine_blocked",
        }
    ),
    "provider_circuit_state": frozenset(
        {"not_configured", "closed", "half_open", "open", "unavailable", "unknown"}
    ),
    "validation_quarantine_state": frozenset(
        {"not_configured", "closed", "half_open", "open", "unavailable", "unknown"}
    ),
    "failure_state_store_outcome": frozenset(
        {
            "not_configured",
            "not_queried",
            "available",
            "authorized",
            "blocked",
            "heartbeat_lost",
            "finish_committed",
            "abort_requested",
            "unavailable",
        }
    ),
    "language_bucket": frozenset({"zh_hans", "en", "mixed", "other", "unknown"}),
}

TOKEN_DIMENSIONS = frozenset(
    {
        "source_token_bucket",
        "target_token_bucket",
        "result_token_bucket",
        "source_demand_token_bucket",
        "duplicate_removed_token_bucket",
        "post_dedup_demand_token_bucket",
        "mandatory_bounded_raw_token_bucket",
        "pre_dedup_required_token_bucket",
        "post_dedup_required_token_bucket",
        "business_pre_loss_required_token_bucket",
        "shadow_post_dedup_required_token_bucket",
    }
)
BOOLEAN_DIMENSIONS = frozenset(
    {
        "provider_usage_available",
        "exact_recent_preserved",
        "current_answer_preserved",
    }
)
COUNT_DIMENSIONS = frozenset(
    {
        "selected_unit_count",
        "dropped_unit_count",
        "truncated_unit_count",
        "deduplicated_unit_count",
    }
)
OBSERVATION_KEYS = frozenset(
    {
        *REQUIRED_AGGREGATE_METRICS,
        "measurement_path",
        "provider_usage_available",
        "language_bucket",
    }
)
VERSION_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{0,127}$")
CASE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,127}$")


def _require_mapping(value: Any, *, name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    return dict(value)


def _require_list(value: Any, *, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{name} must be a list")
    return value


def _require_bounded_int(
    value: Any,
    *,
    name: str,
    maximum: int,
) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 0
        or value > maximum
    ):
        raise ValueError(f"{name} must be a bounded non-negative integer")
    return value


def _validate_observation(raw: Mapping[str, Any]) -> dict[str, Any]:
    observation = dict(raw)
    unknown = set(observation) - OBSERVATION_KEYS
    missing = REQUIRED_AGGREGATE_METRICS - set(observation)
    if unknown:
        raise ValueError("observation contains an unapproved field")
    if missing:
        raise ValueError("observation is missing required aggregate metrics")

    for name, allowed in STRING_DIMENSIONS.items():
        if observation.get(name) not in allowed:
            raise ValueError(f"observation {name} is not allowlisted")
    for name in ("policy_version", "intent_schema_version"):
        value = observation.get(name)
        if not isinstance(value, str) or VERSION_RE.fullmatch(value) is None:
            raise ValueError(f"observation {name} is invalid")
    for name in TOKEN_DIMENSIONS:
        if observation.get(name) not in TOKEN_BUCKETS:
            raise ValueError(f"observation {name} is not a bounded token bucket")
    if observation.get("compression_ratio_bucket") not in RATIO_BUCKETS:
        raise ValueError("observation compression ratio is not bounded")
    if observation.get("latency_bucket") not in LATENCY_BUCKETS:
        raise ValueError("observation latency is not bounded")
    for name in BOOLEAN_DIMENSIONS:
        if not isinstance(observation.get(name), bool):
            raise ValueError(f"observation {name} must be boolean")
    for name in COUNT_DIMENSIONS:
        _require_bounded_int(observation.get(name), name=name, maximum=10_000)
    _require_bounded_int(
        observation.get("estimated_input_tokens"),
        name="estimated_input_tokens",
        maximum=10_000_000,
    )
    _require_bounded_int(
        observation.get("estimator_error_basis_points"),
        name="estimator_error_basis_points",
        maximum=100_000,
    )
    for name in (
        "business_utilization_basis_points",
        "shadow_post_dedup_utilization_basis_points",
    ):
        _require_bounded_int(observation.get(name), name=name, maximum=10_000)

    actual = observation.get("provider_input_tokens_when_available")
    if actual is not None:
        _require_bounded_int(
            actual,
            name="provider_input_tokens_when_available",
            maximum=10_000_000,
        )
    if observation["provider_usage_available"] != (actual is not None):
        raise ValueError("provider usage availability is inconsistent")
    return observation


def _validate_case(case: Mapping[str, Any]) -> dict[str, Any]:
    validated = dict(case)
    case_id = validated.get("case_id")
    if not isinstance(case_id, str) or CASE_ID_RE.fullmatch(case_id) is None:
        raise ValueError("case_id must be a stable synthetic identifier")
    if validated.get("category") not in EXPECTED_CATEGORIES:
        raise ValueError("case category is not allowlisted")
    if validated.get("workflow") not in STRING_DIMENSIONS["workflow"]:
        raise ValueError("case workflow is not allowlisted")
    if validated.get("operation") not in STRING_DIMENSIONS["operation"]:
        raise ValueError("case operation is not allowlisted")
    if validated.get("language_bucket") not in STRING_DIMENSIONS["language_bucket"]:
        raise ValueError("case language bucket is not allowlisted")

    sources = _require_list(validated.get("sources"), name="case sources")
    if not sources or not all(isinstance(item, Mapping) for item in sources):
        raise ValueError("case sources must be non-empty synthetic objects")
    intent = _require_mapping(validated.get("intent"), name="case intent")
    if not isinstance(intent.get("required_fact_ids"), list):
        raise ValueError("case intent must declare required synthetic facts")

    fixture = _require_mapping(validated.get("fake_provider"), name="fake provider")
    if fixture.get("outcome") not in {
        "success",
        "timeout",
        "invalid_json",
        "lease_loss",
        "not_called",
    }:
        raise ValueError("fake provider outcome is not allowlisted")
    expected = _require_mapping(validated.get("expected"), name="case expected")
    attempts = _require_bounded_int(
        expected.get("provider_attempt_count"),
        name="provider_attempt_count",
        maximum=1,
    )
    should_attempt = fixture.get("provider_attempted", True)
    if not isinstance(should_attempt, bool) or should_attempt != (attempts == 1):
        raise ValueError("fake provider attempt contract is inconsistent")
    for name in (
        "selected_fact_ids",
        "excluded_fact_ids",
        "preserved_literals",
        "baseline_selected_fact_ids",
    ):
        if not isinstance(expected.get(name), list):
            raise ValueError(f"case expected {name} must be a list")
    if expected.get("route") not in STRING_DIMENSIONS["route"]:
        raise ValueError("case expected route is not allowlisted")
    if expected.get("validation_outcome") not in STRING_DIMENSIONS[
        "validation_outcome"
    ]:
        raise ValueError("case validation outcome is not allowlisted")
    if expected.get("fallback_outcome") not in STRING_DIMENSIONS[
        "fallback_outcome"
    ]:
        raise ValueError("case fallback outcome is not allowlisted")
    return validated


def load_dataset(path: str | Path) -> dict[str, Any]:
    """Load and fail closed on an unsafe or ambiguous synthetic dataset."""

    dataset_path = Path(path)
    try:
        loaded = json.loads(dataset_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("dataset is not valid UTF-8 JSON") from exc
    dataset = _require_mapping(loaded, name="dataset")
    if dataset.get("schema_version") != DATASET_SCHEMA_VERSION:
        raise ValueError("dataset schema version is unsupported")
    if dataset.get("synthetic_only") is not True:
        raise ValueError("dataset must be synthetic only")
    if dataset.get("model_judge_authoritative") is not False:
        raise ValueError("model judge must remain advisory")
    required = dataset.get("required_aggregate_metrics")
    if not isinstance(required, list) or set(required) != REQUIRED_AGGREGATE_METRICS:
        raise ValueError("dataset required aggregate metrics are incomplete")

    defaults = _require_mapping(
        dataset.get("observation_defaults"),
        name="observation defaults",
    )
    cases = _require_list(dataset.get("cases"), name="dataset cases")
    raw_case_ids = [
        case.get("case_id")
        for case in cases
        if isinstance(case, Mapping)
    ]
    if len(raw_case_ids) != len(cases):
        raise ValueError("dataset contains an invalid case")
    if len(raw_case_ids) != len(set(raw_case_ids)):
        raise ValueError("dataset contains a duplicate case_id")
    if len(cases) != 16:
        raise ValueError("dataset must contain exactly sixteen synthetic cases")
    validated_cases = [
        _validate_case(_require_mapping(case, name="case"))
        for case in cases
    ]
    if {case["category"] for case in validated_cases} != EXPECTED_CATEGORIES:
        raise ValueError("dataset category coverage is incomplete")
    if not FAILURE_CATEGORIES <= {case["category"] for case in validated_cases}:
        raise ValueError("dataset failure coverage is incomplete")

    for case in validated_cases:
        expected = case["expected"]
        observation = {
            **defaults,
            **_require_mapping(
                expected.get("observation_overrides", {}),
                name="observation overrides",
            ),
        }
        if observation.get("operation") != case["operation"]:
            raise ValueError("observation operation conflicts with its case")
        if observation.get("workflow") != case["workflow"]:
            raise ValueError("observation workflow conflicts with its case")
        if observation.get("language_bucket") != case["language_bucket"]:
            raise ValueError("observation language conflicts with its case")
        if observation.get("route") != expected["route"]:
            raise ValueError("observation route conflicts with its case")
        if observation.get("validation_outcome") != expected["validation_outcome"]:
            raise ValueError("observation validation conflicts with its case")
        if observation.get("fallback_outcome") != expected["fallback_outcome"]:
            raise ValueError("observation fallback conflicts with its case")
        _validate_observation(observation)

    return {
        "schema_version": dataset["schema_version"],
        "synthetic_only": True,
        "model_judge_authoritative": False,
        "required_aggregate_metrics": list(required),
        "observation_defaults": defaults,
        "cases": validated_cases,
    }


def _source_fact_ids(case: Mapping[str, Any]) -> set[str]:
    result = set()
    for source in case["sources"]:
        fact_id = source.get("fact_id")
        if isinstance(fact_id, str):
            result.add(fact_id)
    return result


def _source_texts(case: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(
        source["text"]
        for source in case["sources"]
        if isinstance(source.get("text"), str)
    )


def _is_grounded(value: Any, source_texts: tuple[str, ...]) -> bool:
    return isinstance(value, str) and bool(value) and any(
        value in source for source in source_texts
    )


def _evaluate_payload(
    case: Mapping[str, Any],
    response: Any,
) -> tuple[str, set[str], bool]:
    if isinstance(response, str):
        try:
            response = json.loads(response)
        except json.JSONDecodeError:
            return "invalid_json", set(), True
    if not isinstance(response, Mapping):
        return "invalid_json", set(), False

    source_texts = _source_texts(case)
    selected = response.get("selected_fact_ids")
    literals = response.get("preserved_literals")
    excerpts = response.get("supporting_excerpts")
    if not isinstance(selected, list) or not all(isinstance(item, str) for item in selected):
        return "invalid_schema", set(), False
    if not isinstance(literals, list) or not all(isinstance(item, str) for item in literals):
        return "invalid_schema", set(selected), False
    if not isinstance(excerpts, list) or not all(isinstance(item, str) for item in excerpts):
        return "invalid_schema", set(selected), False
    if any(not _is_grounded(item, source_texts) for item in excerpts):
        return "unsupported_excerpt", set(selected), True
    if any(not _is_grounded(item, source_texts) for item in literals):
        return "numeric_literal_changed", set(selected), True

    expected = case["expected"]
    expected_selected = set(expected["selected_fact_ids"])
    expected_literals = set(expected["preserved_literals"])
    excluded = set(expected["excluded_fact_ids"])
    selected_set = set(selected)
    quality_ok = (
        selected_set == expected_selected
        and selected_set.isdisjoint(excluded)
        and expected_literals <= set(literals)
        and selected_set <= _source_fact_ids(case)
    )
    required_unresolved = set(expected.get("required_unresolved_fact_ids", []))
    if required_unresolved:
        unresolved = response.get("unresolved_fact_ids")
        quality_ok = quality_ok and isinstance(unresolved, list) and (
            required_unresolved <= set(unresolved)
        )
    return "valid", selected_set, bool(quality_ok)


def _safe_observation_for_report(observation: Mapping[str, Any]) -> dict[str, Any]:
    """Project only bounded values; avoid even privacy-sensitive key names."""

    safe = dict(observation)
    current_preserved = safe.pop("current_answer_preserved")
    safe["current_response_preserved"] = current_preserved
    return safe


def _basis_points(numerator: int, denominator: int) -> int:
    if denominator <= 0:
        return 0
    return round(numerator * 10_000 / denominator)


def evaluate_dataset(
    dataset: Mapping[str, Any],
    provider: Callable[[dict[str, Any]], Any],
    clock: Callable[[], datetime],
) -> dict[str, Any]:
    """Evaluate one validated synthetic dataset using only the supplied replay."""

    if not callable(provider):
        raise TypeError("provider must be a callable fake replay")
    if not callable(clock):
        raise TypeError("clock must be callable")
    now = clock()
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise ValueError("acceptance clock must return a timezone-aware datetime")

    data = _require_mapping(dataset, name="dataset")
    if data.get("synthetic_only") is not True:
        raise ValueError("dataset must be synthetic only")
    if data.get("model_judge_authoritative") is not False:
        raise ValueError("model judge must remain advisory")
    defaults = _require_mapping(data.get("observation_defaults"), name="observation defaults")
    cases = _require_list(data.get("cases"), name="dataset cases")
    case_ids = [case.get("case_id") for case in cases if isinstance(case, Mapping)]
    if len(case_ids) != len(cases) or len(case_ids) != len(set(case_ids)):
        raise ValueError("dataset contains a duplicate or invalid case_id")

    observations: list[dict[str, Any]] = []
    route_counts: Counter[str] = Counter()
    fallback_counts: Counter[str] = Counter()
    failure_counts: Counter[str] = Counter()
    passed_count = 0
    fake_attempt_count = 0
    max_attempt_count = 0
    provider_retry_count = 0
    judge_disagreements = 0
    required_total = 0
    task_relevant = 0
    baseline_relevant = 0
    task_selected_total = 0
    baseline_selected_total = 0

    for raw_case in cases:
        case = _validate_case(_require_mapping(raw_case, name="case"))
        expected = case["expected"]
        fixture = case["fake_provider"]
        expected_attempts = expected["provider_attempt_count"]
        attempts = 0
        response: Any = None
        timed_out = False
        unexpected_provider_failure = False

        if expected_attempts == 1:
            attempts = 1
            fake_attempt_count += 1
            try:
                response = provider(case)
            except TimeoutError:
                timed_out = True
            except Exception:
                unexpected_provider_failure = True

        max_attempt_count = max(max_attempt_count, attempts)
        provider_retry_count += max(0, attempts - 1)

        fixture_outcome = fixture["outcome"]
        quality_ok = True
        if attempts == 0:
            if fixture_outcome == "lease_loss":
                actual_validation = "lease_lost"
                actual_route = "artifact_fallback"
                actual_fallback = "deterministic"
            elif fixture_outcome == "not_called":
                actual_validation = "not_run"
                actual_route = "compression_bypassed"
                actual_fallback = "not_used"
            else:
                actual_validation = "unavailable"
                actual_route = "artifact_fallback"
                actual_fallback = "deterministic"
                quality_ok = False
            task_selected = set(case["intent"]["required_fact_ids"])
        elif timed_out:
            actual_validation = "not_run"
            actual_route = "artifact_fallback"
            actual_fallback = "deterministic"
            task_selected = set(case["intent"]["required_fact_ids"])
            quality_ok = fixture_outcome == "timeout"
        elif unexpected_provider_failure:
            actual_validation = "unavailable"
            actual_route = "artifact_fallback"
            actual_fallback = "deterministic"
            task_selected = set()
            quality_ok = False
        else:
            actual_validation, provider_selected, quality_ok = _evaluate_payload(
                case,
                response,
            )
            if actual_validation == "valid":
                actual_route = "artifact_created"
                actual_fallback = "not_used"
                task_selected = provider_selected
            else:
                actual_route = "artifact_fallback"
                actual_fallback = "deterministic"
                task_selected = set(case["intent"]["required_fact_ids"])

        required = set(case["intent"]["required_fact_ids"])
        sources_cover_required = required <= _source_fact_ids(case)
        case_passed = bool(
            attempts == expected_attempts
            and actual_validation == expected["validation_outcome"]
            and actual_route == expected["route"]
            and actual_fallback == expected["fallback_outcome"]
            and sources_cover_required
            and quality_ok
        )
        passed_count += int(case_passed)

        deterministic_accepts_model_output = actual_validation == "valid"
        advisory_accepts = case["advisory_judge"].get("verdict") == "pass"
        judge_disagreements += int(
            advisory_accepts != deterministic_accepts_model_output
        )

        baseline_selected = set(expected["baseline_selected_fact_ids"])
        required_total += len(required)
        task_relevant += len(task_selected & required)
        baseline_relevant += len(baseline_selected & required)
        task_selected_total += len(task_selected)
        baseline_selected_total += len(baseline_selected)

        route_counts[actual_route] += 1
        fallback_counts[actual_fallback] += 1
        if case["category"] in FAILURE_CATEGORIES:
            failure_counts[SAFE_FAILURE_CATEGORY[case["category"]]] += 1

        observation = {
            **defaults,
            **_require_mapping(
                expected.get("observation_overrides", {}),
                name="observation overrides",
            ),
        }
        usage = fixture.get("usage")
        if attempts == 1 and isinstance(usage, Mapping):
            actual_tokens = usage.get("input_tokens")
            if isinstance(actual_tokens, int) and not isinstance(actual_tokens, bool) and actual_tokens >= 0:
                observation["provider_usage_available"] = True
                observation["provider_input_tokens_when_available"] = actual_tokens
                estimated = observation["estimated_input_tokens"]
                observation["estimator_error_basis_points"] = round(
                    abs(estimated - actual_tokens) * 10_000 / max(1, actual_tokens)
                )
        else:
            observation["provider_usage_available"] = False
            observation["provider_input_tokens_when_available"] = None
            observation["estimator_error_basis_points"] = 0
        validated_observation = _validate_observation(observation)
        observations.append(_safe_observation_for_report(validated_observation))

    failed_count = len(cases) - passed_count
    real_provider_calls = getattr(provider, "real_provider_call_count", 0)
    if isinstance(real_provider_calls, bool) or not isinstance(real_provider_calls, int):
        real_provider_calls = 0
    real_provider_calls = max(0, real_provider_calls)

    task_relevance = _basis_points(task_relevant, max(1, task_selected_total))
    baseline_relevance = _basis_points(
        baseline_relevant,
        max(1, baseline_selected_total),
    )
    task_preservation = _basis_points(task_relevant, max(1, required_total))
    baseline_preservation = _basis_points(
        baseline_relevant,
        max(1, required_total),
    )
    overall_passed = (
        failed_count == 0
        and real_provider_calls == 0
        and task_relevance > baseline_relevance
        and task_preservation > baseline_preservation
    )

    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "dataset_schema_version": data.get("schema_version"),
        "overall_status": "passed" if overall_passed else "failed",
        "synthetic_case_count": len(cases),
        "real_provider_call_count": real_provider_calls,
        "fake_provider_attempt_count": fake_attempt_count,
        "aggregates": {
            "passed_case_count": passed_count,
            "failed_case_count": failed_count,
            "task_aware_relevance_basis_points": task_relevance,
            "baseline_relevance_basis_points": baseline_relevance,
            "task_aware_preservation_basis_points": task_preservation,
            "baseline_preservation_basis_points": baseline_preservation,
            "advisory_judge_disagreement_count": judge_disagreements,
            "failure_category_counts": dict(sorted(failure_counts.items())),
            "route_counts": dict(sorted(route_counts.items())),
            "fallback_outcome_counts": dict(sorted(fallback_counts.items())),
            "max_provider_attempt_count": max_attempt_count,
            "provider_retry_count": provider_retry_count,
        },
        "observations": observations,
    }


def run_acceptance(
    dataset_path: str | Path,
    provider: Callable[[dict[str, Any]], Any],
    clock: Callable[[], datetime],
) -> dict[str, Any]:
    """Load the declared synthetic dataset and run the offline replay gate."""

    return evaluate_dataset(load_dataset(dataset_path), provider, clock)
