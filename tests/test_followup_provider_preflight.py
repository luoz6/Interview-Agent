from pathlib import Path

import pytest

from app.services.followup_provider_preflight import (
    DeepSeekDiscoverySnapshot,
    ProviderPrice,
    estimate_provider_cost,
    evaluate_followup_provider_preflight,
    parse_deepseek_pricing_table,
)
from app.services.interview_quality_dataset import load_interview_quality_dataset
from app.services.interview_quality_provider_authorization import (
    load_provider_authorization,
)


DATASET = Path(
    "tests/golden/interview_quality_v1/followup-decision-quality-v2.json"
)
GATE = Path("config/interview_quality_v1_gate.json")
AUTH = Path("config/interview_quality_v1_provider_authorization.json")
DATASET_MANIFEST = Path("tests/golden/interview_quality_v1/manifest.json")
EXECUTION_MANIFEST = Path("docs/interview-quality-v1-execution-manifest.json")


def discovery(*, models=("deepseek-chat",), priced=True, error_code=None):
    prices = (
        {
            "deepseek-chat": ProviderPrice(
                cache_hit_input_per_million=0.1,
                cache_miss_input_per_million=0.2,
                output_per_million=0.3,
            )
        }
        if priced
        else {}
    )
    return DeepSeekDiscoverySnapshot(
        observed_at="2026-08-05T00:00:00Z",
        models_endpoint_ok=True,
        model_ids=list(models),
        pricing_page_ok=True,
        prices=prices,
        error_code=error_code,
    )


def evaluate(snapshot):
    return evaluate_followup_provider_preflight(
        manifest=load_provider_authorization(AUTH),
        dataset=load_interview_quality_dataset(DATASET),
        dataset_path=DATASET,
        gate_config_path=GATE,
        authorization_path=AUTH,
        dataset_file_manifest_path=DATASET_MANIFEST,
        execution_manifest_path=EXECUTION_MANIFEST,
        discovery=snapshot,
        credential_present=True,
        evidence_persistence_available=True,
        environment_model="deepseek-v4-pro",
    )


def test_preflight_accepts_only_frozen_model_hashes_and_redacted_dataset():
    result = evaluate(discovery())

    assert result.allowed is True
    assert result.hard_stop_conditions == []
    assert result.dataset_manifest_match is True
    assert result.gate_config_manifest_match is True
    assert result.authorization_manifest_match is True
    assert result.redaction_preflight_passed is True
    assert result.environment_model_ignored is True
    assert result.authorized_model == "deepseek-chat"


def test_current_official_model_list_causes_model_drift_before_requests():
    result = evaluate(
        discovery(
            models=("deepseek-v4-flash", "deepseek-v4-pro"),
            priced=False,
        )
    )

    assert result.allowed is False
    assert result.model_available is False
    assert result.pricing_available is False
    assert result.hard_stop_conditions == ["MODEL_VERSION_DRIFT"]


def test_missing_price_for_available_model_stops_cost_accounting():
    result = evaluate(discovery(priced=False))

    assert result.model_available is True
    assert result.hard_stop_conditions == ["USAGE_METERING_UNAVAILABLE"]


def test_discovery_outage_is_not_misreported_as_model_drift():
    snapshot = DeepSeekDiscoverySnapshot(
        observed_at="2026-08-05T00:00:00Z",
        models_endpoint_ok=False,
        model_request_attempts=3,
        model_ids=[],
        pricing_page_ok=True,
        pricing_request_attempts=1,
        prices={},
        error_code="network",
    )

    result = evaluate(snapshot)

    assert "MODEL_VERSION_DRIFT" not in result.hard_stop_conditions
    assert result.hard_stop_conditions == ["REPEATED_PROVIDER_FAILURE"]


def test_official_pricing_table_parser_maps_model_columns():
    content = """
    <table><tr><td colspan="2">MODEL</td><td>deepseek-a</td><td>deepseek-b</td></tr>
    <tr><td rowspan="3">PRICING</td><td>1M INPUT TOKENS (CACHE HIT)</td>
    <td>$0.01</td><td>$0.02</td></tr>
    <tr><td>1M INPUT TOKENS (CACHE MISS)</td><td>$0.10</td><td>$0.20</td></tr>
    <tr><td>1M OUTPUT TOKENS</td><td>$0.30</td><td>$0.40</td></tr></table>
    """

    prices = parse_deepseek_pricing_table(content)

    assert prices["deepseek-a"].cache_hit_input_per_million == 0.01
    assert prices["deepseek-b"].cache_miss_input_per_million == 0.20
    assert prices["deepseek-b"].output_per_million == 0.40


def test_cost_estimate_separates_cached_and_uncached_input():
    price = ProviderPrice(
        cache_hit_input_per_million=1,
        cache_miss_input_per_million=2,
        output_per_million=3,
    )

    assert estimate_provider_cost(
        price=price,
        input_tokens=1_000_000,
        cached_input_tokens=250_000,
        output_tokens=1_000_000,
    ) == pytest.approx(4.75)
