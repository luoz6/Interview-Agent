from __future__ import annotations

import pytest

from app.services.memory_shadow_observability import MemoryShadowObservabilityService
from app.domain.memory.contracts import (
    ALLOWED_TAXONOMY,
    canonical_principal_fact,
)
from scripts.memory_shadow_security_review import PROTECTED_CATEGORY_TERMS
from tests.memory_shadow_fixtures import evidence_bundle


def test_taxonomy_has_no_protected_hiring_scoring_or_personality_categories():
    flattened = {
        value.casefold().replace("_", "-")
        for key, values in ALLOWED_TAXONOMY.items()
        for value in (key, *values)
    }
    assert flattened.isdisjoint(PROTECTED_CATEGORY_TERMS)


@pytest.mark.parametrize(
    "key",
    [
        "personality",
        "integrity",
        "mental_health",
        "physical_health",
        "politics",
        "religion",
        "ethnicity",
        "marital_status",
        "pregnancy",
        "age",
        "hiring_recommendation",
        "historical_score",
    ],
)
def test_protected_or_hiring_fact_keys_are_rejected(key):
    with pytest.raises(ValueError, match="approved taxonomy"):
        canonical_principal_fact({key: "synthetic"})


def test_privacy_sensitive_review_label_is_a_hard_stop_not_low_quality_only():
    evidence = evidence_bundle()
    evidence["quality"]["privacy_sensitive_count"] = 1

    result = MemoryShadowObservabilityService().build_status(evidence)

    assert result["automatic_stop"]["triggered"] is True
    assert "PROPOSAL_PRIVACY_SENSITIVE" in result["automatic_stop"]["gate_codes"]
    assert result["automatic_stop"]["privacy_notification_required"] is True
    assert result["automatic_stop"]["expansion_allowed"] is False
