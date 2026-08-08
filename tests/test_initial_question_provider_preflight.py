from __future__ import annotations

from pathlib import Path

from app.services.followup_provider_preflight import (
    DeepSeekDiscoverySnapshot,
    ProviderPrice,
)
from app.services.initial_question_provider_preflight import (
    evaluate_initial_question_provider_preflight,
)
from app.services.interview_quality_dataset import load_interview_quality_dataset
from app.services.interview_quality_provider_authorization import (
    load_provider_authorization,
)


DATASET = Path("tests/golden/interview_quality_v1/initial-question-quality-v2.json")
AUTHORIZATION = Path("config/interview_quality_v1_provider_authorization.json")
GATE = Path("config/interview_quality_v1_gate.json")
DATASET_MANIFEST = Path("tests/golden/interview_quality_v1/manifest.json")
EXECUTION_MANIFEST = Path("docs/interview-quality-v1-execution-manifest.json")


def discovery(*, model_ids=("deepseek-v4-pro",), priced=("deepseek-v4-pro",)):
    price = ProviderPrice(
        cache_hit_input_per_million=0.1,
        cache_miss_input_per_million=0.2,
        output_per_million=0.3,
    )
    return DeepSeekDiscoverySnapshot(
        observed_at="2026-08-06T00:00:00Z",
        models_endpoint_ok=True,
        model_ids=list(model_ids),
        pricing_page_ok=True,
        prices={model: price for model in priced},
    )


def evaluate(snapshot):
    return evaluate_initial_question_provider_preflight(
        manifest=load_provider_authorization(AUTHORIZATION),
        dataset=load_interview_quality_dataset(DATASET),
        dataset_path=DATASET,
        gate_config_path=GATE,
        authorization_path=AUTHORIZATION,
        dataset_file_manifest_path=DATASET_MANIFEST,
        execution_manifest_path=EXECUTION_MANIFEST,
        discovery=snapshot,
        credential_present=True,
        evidence_persistence_available=True,
        environment_model="deepseek-v4-pro",
    )


def test_t57_preflight_accepts_only_exact_authorized_model_and_safe_categories():
    result = evaluate(discovery())

    assert result.allowed is True
    assert result.task == "T57"
    assert result.data_categories == (
        "public_technical_material",
        "synthetic_job_descriptions",
        "synthetic_resumes",
    )
    assert result.environment_model_ignored is False
    assert result.authorized_model == "deepseek-v4-pro"
    assert result.dataset_manifest_match is True
    assert result.gate_config_manifest_match is True
    assert result.authorization_manifest_match is True


def test_catalog_without_exact_v4_pro_is_model_version_drift_without_fallback():
    result = evaluate(
        discovery(
            model_ids=("deepseek-chat", "deepseek-v4-flash"),
            priced=("deepseek-chat", "deepseek-v4-flash"),
        )
    )

    assert result.allowed is False
    assert result.model_available is False
    assert result.hard_stop_conditions == ("MODEL_VERSION_DRIFT",)


def test_available_but_unpriced_authorized_model_stops_usage_metering():
    result = evaluate(discovery(priced=()))

    assert result.hard_stop_conditions == ("USAGE_METERING_UNAVAILABLE",)
