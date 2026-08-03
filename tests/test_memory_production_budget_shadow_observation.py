from copy import deepcopy
import json
from pathlib import Path

import pytest

from scripts.memory_production_budget_shadow_observation import (
    AggregateInputBlocked,
    BOUNDARY_FIELDS,
    INPUT_SCHEMA_VERSION,
    OUTPUT_SCHEMA_VERSION,
    sanitize_aggregate_input,
    validate_aggregate_input,
    validate_observation_artifact,
)


FIXTURE = Path(
    "tests/fixtures/memory_production_budget_shadow/pass_candidate.json"
)


def aggregate_input():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_staging_observation_contract_cannot_be_reused_as_production_input():
    staging = json.loads(
        Path("docs/memory-budget-shadow-observation.json").read_text(
            encoding="utf-8"
        )
    )

    assert staging["data_category"] == "synthetic"
    assert staging["provider_calls"] == 0
    assert staging["production_observation"] == "NOT_RUN"
    with pytest.raises(AggregateInputBlocked) as raised:
        validate_aggregate_input(staging)
    assert "AGGREGATE_INPUT_SCHEMA_INVALID" in raised.value.codes
    assert "AGGREGATE_DATA_CATEGORY_INVALID" in raised.value.codes


def test_valid_aggregate_is_sanitized_to_a_separate_schema():
    source = aggregate_input()
    result = sanitize_aggregate_input(source)

    assert source["schema_version"] == INPUT_SCHEMA_VERSION
    assert result.artifact["schema_version"] == OUTPUT_SCHEMA_VERSION
    assert result.input_field_count == len(source)
    for key, expected in BOUNDARY_FIELDS.items():
        assert result.artifact[key] == expected
    validate_observation_artifact(result.artifact)


@pytest.mark.parametrize(
    ("mutator", "code"),
    [
        (
            lambda value: value.update({"schema_version": "unknown"}),
            "AGGREGATE_INPUT_SCHEMA_INVALID",
        ),
        (
            lambda value: value.update({"data_category": "candidate"}),
            "AGGREGATE_DATA_CATEGORY_INVALID",
        ),
        (
            lambda value: value.update({"requested_phase": "WRITE_SHADOW"}),
            "REQUESTED_PHASE_NOT_BUDGET_ONLY",
        ),
        (
            lambda value: value.update({"approved_revision": "main"}),
            "APPROVED_REVISION_INVALID",
        ),
        (
            lambda value: value.update({"approved_traffic_percent": 2.0}),
            "APPROVED_TRAFFIC_PERCENT_INVALID",
        ),
        (
            lambda value: value.update({"dsn": "postgresql://private"}),
            "AGGREGATE_INPUT_FIELD_SET_INVALID",
        ),
        (
            lambda value: value.update(
                {"requested_phase": "sk-private-example-value"}
            ),
            "AGGREGATE_SENSITIVE_VALUE_DETECTED",
        ),
        (
            lambda value: value["language_sample_counts"].update(
                {"principal-123": 1}
            ),
            "LANGUAGE_BUCKETS_INVALID",
        ),
    ],
)
def test_invalid_or_sensitive_aggregate_input_is_blocked(mutator, code):
    value = aggregate_input()
    mutator(value)

    with pytest.raises(AggregateInputBlocked) as raised:
        validate_aggregate_input(value)

    assert code in raised.value.codes


def test_observation_boundary_cannot_claim_principal_authorization():
    artifact = sanitize_aggregate_input(aggregate_input()).artifact
    changed = deepcopy(artifact)
    changed["principal_write_shadow_production"] = "AUTHORIZED"

    with pytest.raises(AggregateInputBlocked) as raised:
        validate_observation_artifact(changed)

    assert any("BOUNDARY_INVALID" in code for code in raised.value.codes)


def test_zero_observed_traffic_is_structurally_valid_for_continue_decision():
    value = aggregate_input()
    value["observed_traffic_percent_max"] = 0.0

    validate_aggregate_input(value)
