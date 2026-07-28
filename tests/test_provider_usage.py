from types import SimpleNamespace

from app.services.provider_usage import (
    begin_provider_attempt,
    consume_provider_context_metadata,
    publish_provider_response,
    reset_provider_context_metadata,
)


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
