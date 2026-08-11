import json

from app.adapters.memory.principal_memory import InMemoryPrincipalMemoryFactStore
from app.services.in_memory_principal_memory_consent import InMemoryPrincipalMemoryConsentStore
from app.runtime.config.memory import load_effective_memory_config
from scripts.principal_memory_write_shadow import (
    build_write_shadow_payload, run_fault_matrix, run_write_shadow, validate_artifact,
    validate_write_axis, write_shadow_environment,
)

def test_write_shadow_is_the_only_enabled_axis():
    config = load_effective_memory_config(write_shadow_environment())
    assert validate_write_axis(config) == []
    assert config.long_term.read_shadow_enabled is False
    assert config.long_term.trusted_local_api_enabled is False


def test_fault_matrix_cancels_or_rejects_every_unsafe_case():
    result = run_fault_matrix()
    assert result == {
        "candidate_rejected": 3,
        "consent_unavailable": 1,
        "extractor_failure_contained": 1,
        "identity_changed": 1,
        "identity_unavailable": 1,
        "source_unavailable": 1,
        "source_version_changed": 1,
    }


def test_synthetic_write_shadow_creates_only_deduplicated_proposals():
    record = run_write_shadow(
        fact_store=InMemoryPrincipalMemoryFactStore(),
        consent_store=InMemoryPrincipalMemoryConsentStore(),
        sample_count=30,
    )
    validate_artifact(record)
    assert record["proposal_created_count"] == 30
    assert record["proposed_fact_count"] == 30
    assert record["duplicate_fact_count"] == 0
    assert record["provider_calls"] == 0
    assert not any(record["hard_invariants"].values())
    assert "principal_id" not in json.dumps(record)
    record["cleanup_residue"] = 0
    record["rollback_verified"] = True
    payload = build_write_shadow_payload(record)
    assert payload.sample_count == 30
    assert payload.violations == []
    assert payload.synthetic is True
    assert payload.metrics["fault_candidate_rejected"] == 3.0
    assert payload.metrics["hard_cross_principal_write"] == 0.0
    assert payload.metrics["cleanup_residue"] == 0.0


def test_write_shadow_payload_rejects_string_counts_and_derives_invariant_gate():
    record = run_write_shadow(
        fact_store=InMemoryPrincipalMemoryFactStore(),
        consent_store=InMemoryPrincipalMemoryConsentStore(),
        sample_count=3,
    )
    record["cleanup_residue"] = 0
    record["rollback_verified"] = True
    record["hard_invariants"]["privacy_artifact_hit"] = 1
    payload = build_write_shadow_payload(record)
    assert "WRITE_SHADOW_PRIVACY_HIT" in payload.violations

    record["hard_invariants"]["privacy_artifact_hit"] = "0"
    try:
        build_write_shadow_payload(record)
    except ValueError:
        pass
    else:
        raise AssertionError("string hard-invariant count was accepted")
