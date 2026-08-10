from app.services.trace_sanitization import sanitize_agent_safe_metadata


def test_memory_private_fields_are_rejected_without_stringifying_values():
    marker = "PRIVATE-MEMORY-CONTENT-937"
    payload = {
        "prompt": marker,
        "answer": marker,
        "summary": marker,
        "excerpt": marker,
        "session_id": marker,
        "evidence_id": marker,
        "artifact_ref": marker,
        "credential": marker,
        "dsn": marker,
        "estimated_input_tokens": 42,
    }

    result = sanitize_agent_safe_metadata(payload)

    assert result.value == {"estimated_input_tokens": 42}
    assert marker not in repr(result)
    assert result.rejected_count == 9


def test_principal_memory_private_fields_are_blocked():
    marker = "PRIVATE-PRINCIPAL-553"
    result = sanitize_agent_safe_metadata(
        {
            "principal_id": marker,
            "fact_id": marker,
            "normalized_fact": marker,
            "source_excerpt_sha256": marker,
            "selected_count": 1,
        }
    )
    assert result.value == {"selected_count": 1}
    assert marker not in repr(result)


def test_followup_performance_metadata_is_safe_but_payloads_remain_blocked():
    marker = "PRIVATE-FOLLOWUP-PAYLOAD-771"
    result = sanitize_agent_safe_metadata(
        {
            "provider_model": "deepseek-v4-pro",
            "provider_cached_input_tokens": 64,
            "first_item_latency_ms": 12.5,
            "prompt": marker,
            "raw_response": marker,
            "content": marker,
            "api_key": marker,
        }
    )

    assert result.value == {
        "provider_model": "deepseek-v4-pro",
        "provider_cached_input_tokens": 64,
        "first_item_latency_ms": 12.5,
    }
    assert result.rejected_count == 4
    assert marker not in repr(result)


def test_failure_containment_capabilities_and_digests_are_blocked():
    marker = "PRIVATE-FAILURE-STATE-779"
    private = {
        "state_key_sha256": marker,
        "privacy_scope_sha256": marker,
        "owner_key_sha256": marker,
        "probe_owner_sha256": marker,
        "probe_token": marker,
        "failure_state_record": {"raw_error": marker},
        "provider_error": marker,
        "validation_payload": marker,
        "failure_code": "provider_timeout",
        "store_outcome": "opened",
    }

    result = sanitize_agent_safe_metadata(private)

    assert result.value == {
        "failure_code": "provider_timeout",
        "store_outcome": "opened",
    }
    assert marker not in repr(result)
    assert result.rejected_count == 8
