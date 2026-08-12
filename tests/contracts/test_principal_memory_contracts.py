from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.ports.principal_memory import PrincipalMemoryFactStore
from app.domain.memory.contracts import (
    CANONICALIZATION_VERSION,
    CONSENT_POLICY_VERSION,
    TAXONOMY_VERSION,
    PrincipalMemoryFact,
    canonical_principal_fact,
    derive_principal_fact_id,
)


def _fact(**overrides):
    normalized = canonical_principal_fact({"confirmed_skill": "python"})
    values = {
        "deployment_id": "single-tenant-local",
        "principal_id": "principal-test",
        "fact_type": "confirmed_skill",
        "normalized_fact": normalized,
        "source_manifest_sha256": "a" * 64,
        "source_excerpt_sha256": "b" * 64,
        "consent_policy_version": CONSENT_POLICY_VERSION,
        "taxonomy_version": TAXONOMY_VERSION,
        "canonicalization_version": CANONICALIZATION_VERSION,
    }
    values["fact_id"] = derive_principal_fact_id(**values)
    values.update(
        {
            "confidence": 0.8,
            "authority": "model_proposed",
            "source_session_id": "session-synthetic",
            "created_at": datetime(2026, 7, 30, tzinfo=timezone.utc),
        }
    )
    values.update(overrides)
    return values


def test_canonical_fact_is_nfc_sorted_compact_and_taxonomy_bounded():
    assert canonical_principal_fact({"confirmed_skill": "python"}) == (
        '{"confirmed_skill":"python"}'
    )
    with pytest.raises(ValueError, match="taxonomy"):
        canonical_principal_fact({"company_name": "private-company"})
    with pytest.raises(ValueError, match="taxonomy"):
        canonical_principal_fact({"interview_language": "free text language"})


@pytest.mark.parametrize(
    ("key", "value", "expected"),
    [
        ("focus_topic", "  高并发  缓存一致性  ", "高并发 缓存一致性"),
        ("confirmed_skill", "Kubernetes  1.32", "Kubernetes 1.32"),
        ("learning_goal", "掌握 Kafka 消息可靠性设计", "掌握 Kafka 消息可靠性设计"),
    ],
)
def test_user_declared_text_taxonomies_accept_and_normalize_custom_values(
    key,
    value,
    expected,
):
    assert canonical_principal_fact({key: value}) == json.dumps(
        {key: expected},
        ensure_ascii=False,
        separators=(",", ":"),
    )


@pytest.mark.parametrize("value", ["", "   ", "line one\nline two"])
def test_user_declared_text_taxonomies_reject_blank_or_control_characters(value):
    with pytest.raises(ValueError, match="scalar|characters"):
        canonical_principal_fact({"focus_topic": value})


def test_user_declared_text_taxonomies_enforce_per_field_length_limits():
    with pytest.raises(ValueError, match="bounded"):
        canonical_principal_fact({"focus_topic": "x" * 121})
    assert canonical_principal_fact({"learning_goal": "x" * 160})


def test_principal_fact_identity_is_deterministic_and_active_requires_confirmation():
    first = PrincipalMemoryFact(**_fact())
    second = PrincipalMemoryFact(**_fact())
    assert first.fact_id == second.fact_id

    with pytest.raises(ValidationError, match="user confirmation"):
        PrincipalMemoryFact(**_fact(status="active"))


def test_principal_fact_rejects_noncanonical_json_and_fabricated_identity():
    with pytest.raises(ValidationError, match="canonical"):
        PrincipalMemoryFact(**_fact(normalized_fact='{"confirmed_skill": "python"}'))
    with pytest.raises(ValidationError, match="fact_id"):
        PrincipalMemoryFact(**_fact(fact_id="f" * 64))


def test_principal_memory_fact_store_is_runtime_protocol():
    assert getattr(PrincipalMemoryFactStore, "_is_runtime_protocol", False)
