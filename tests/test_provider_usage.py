from types import SimpleNamespace

from app.services.provider_usage import (
    begin_provider_attempt,
    consume_provider_context_metadata,
    normalize_estimator_error,
    publish_prompt_measurement,
    publish_provider_response,
    publish_plan_context_selection,
    reset_provider_context_metadata,
)


def test_plan_context_selection_records_counts_without_payloads():
    reset_provider_context_metadata()
    publish_plan_context_selection(candidate_count=3, retained_count=2)

    assert consume_provider_context_metadata() == {
        "plan_knowledge_candidate_count": 3,
        "plan_knowledge_retained_count": 2,
    }
from app.services.context_budget import RenderedPromptMeasurement


def test_provider_usage_metadata_is_normalized_without_payloads():
    reset_provider_context_metadata()
    begin_provider_attempt()
    publish_provider_response(
        SimpleNamespace(
            usage_metadata={
                "input_tokens": 120,
                "output_tokens": 30,
                "total_tokens": 150,
            }
        )
    )
    assert consume_provider_context_metadata() == {
        "provider_attempt_count": 1,
        "provider_metered_attempt_count": 1,
        "provider_usage_available": True,
        "provider_input_tokens": 120,
        "provider_output_tokens": 30,
        "provider_total_tokens": 150,
    }


def test_missing_usage_is_explicit_and_does_not_fabricate_actuals():
    reset_provider_context_metadata()
    begin_provider_attempt()
    publish_provider_response(SimpleNamespace(content="secret"))
    metadata = consume_provider_context_metadata()
    assert metadata["provider_usage_available"] is False
    assert "provider_input_tokens" not in metadata
    assert "secret" not in repr(metadata)


def test_provider_model_is_retained_when_usage_is_missing():
    reset_provider_context_metadata()
    publish_provider_response(
        SimpleNamespace(
            content="secret",
            response_metadata={"model_name": "deepseek-v4-pro"},
        )
    )

    assert consume_provider_context_metadata() == {
        "provider_model": "deepseek-v4-pro",
        "provider_unmetered_attempt_count": 1,
        "provider_usage_available": False,
    }


def test_one_metered_response_cannot_hide_an_earlier_unmetered_attempt():
    reset_provider_context_metadata()
    begin_provider_attempt()
    publish_provider_response(SimpleNamespace(content="first"))
    begin_provider_attempt()
    publish_provider_response(
        SimpleNamespace(usage_metadata={"input_tokens": 12, "output_tokens": 3})
    )

    metadata = consume_provider_context_metadata()

    assert metadata["provider_attempt_count"] == 2
    assert metadata["provider_metered_attempt_count"] == 1
    assert metadata["provider_unmetered_attempt_count"] == 1
    assert metadata["provider_usage_available"] is False


def test_cached_input_tokens_and_provider_model_are_normalized():
    reset_provider_context_metadata()
    publish_provider_response(
        SimpleNamespace(
            usage_metadata={
                "input_tokens": 120,
                "output_tokens": 30,
                "input_token_details": {"cache_read": 80},
            },
            response_metadata={"model": "deepseek-v4-pro"},
        )
    )

    metadata = consume_provider_context_metadata()
    assert metadata["provider_cached_input_tokens"] == 80
    assert metadata["provider_model"] == "deepseek-v4-pro"
    assert metadata["provider_total_tokens"] == 150


def test_deepseek_cached_token_alias_is_normalized():
    reset_provider_context_metadata()
    publish_provider_response(
        SimpleNamespace(
            response_metadata={
                "token_usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 20,
                    "prompt_cache_hit_tokens": 60,
                }
            }
        )
    )

    assert consume_provider_context_metadata()["provider_cached_input_tokens"] == 60


def test_provider_usage_records_bucket_and_normalized_estimator_error():
    reset_provider_context_metadata()
    publish_prompt_measurement(
        RenderedPromptMeasurement(
            estimated_input_tokens=150,
            available_input_tokens=1_000,
            budget_utilization_basis_points=1_500,
            estimator_path="conservative_utf8",
            estimator_fallback_used=True,
            prompt_sha256="a" * 64,
        ),
        language_bucket="zh_hans",
    )
    publish_provider_response(
        SimpleNamespace(usage_metadata={"input_tokens": 120})
    )

    metadata = consume_provider_context_metadata()
    assert metadata["language_bucket"] == "zh_hans"
    assert metadata["estimator_error_direction"] == "over"
    assert metadata["estimator_error_basis_points"] == 2_500
    assert "prompt" not in metadata


def test_estimator_error_contract_distinguishes_under_exact_and_over():
    assert normalize_estimator_error(
        estimated_input_tokens=80,
        provider_input_tokens=100,
    ) == {
        "estimator_error_direction": "under",
        "estimator_error_basis_points": 2_000,
    }
    assert normalize_estimator_error(
        estimated_input_tokens=100,
        provider_input_tokens=100,
    )["estimator_error_direction"] == "exact"
    assert normalize_estimator_error(
        estimated_input_tokens=120,
        provider_input_tokens=100,
    )["estimator_error_direction"] == "over"
