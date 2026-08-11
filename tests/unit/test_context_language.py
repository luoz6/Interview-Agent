from app.services.context_language import classify_context_language
from app.services.trace_sanitization import sanitize_agent_safe_metadata


def test_context_language_buckets_are_deterministic_and_bounded():
    assert classify_context_language("请解释缓存失效策略") == "zh_hans"
    assert classify_context_language("Explain the cache invalidation strategy") == "en"
    assert classify_context_language("请解释 Redis fallback strategy") == "mixed"
    assert classify_context_language("12345") == "other"
    assert classify_context_language("   ") == "unknown"
    assert classify_context_language(None) == "unknown"


def test_language_classification_does_not_return_source_text():
    source = "candidate-secret-回答"
    bucket = classify_context_language(source)

    assert bucket == "mixed"
    assert source not in bucket


def test_language_bucket_is_not_persisted_in_correlated_agent_metadata():
    sanitized = sanitize_agent_safe_metadata(
        {"language_bucket": "zh_hans", "estimated_input_tokens": 10}
    )

    assert sanitized.value == {"estimated_input_tokens": 10}
    assert sanitized.rejected_count == 1
