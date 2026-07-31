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
