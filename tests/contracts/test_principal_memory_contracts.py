from __future__ import annotations

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
        canonical_principal_fact({"confirmed_skill": "free text skill"})


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
