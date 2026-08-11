from __future__ import annotations

from pathlib import Path

from app.services.trace_sanitization import sanitize_agent_safe_metadata


def test_trace_sanitizer_blocks_all_principal_fact_and_source_fields():
    marker = "PRIVATE-PRINCIPAL-MARKER-7721"
    payload = {
        "principal_id": marker,
        "fact_id": marker,
        "normalized_fact": marker,
        "source_manifest_sha256": marker,
        "source_excerpt_sha256": marker,
        "consent_record": marker,
        "selected_count": 2,
    }
    result = sanitize_agent_safe_metadata(payload)
    assert marker not in repr(result)
    assert result.value == {"selected_count": 2}


def test_principal_shadow_and_proposal_modules_do_not_log_locators_or_content():
    for name in (
        "app/services/principal_memory_proposals.py",
        "app/services/principal_memory_shadow.py",
    ):
        source = Path(name).read_text(encoding="utf-8")
        assert "logger." not in source
        assert "print(" not in source
