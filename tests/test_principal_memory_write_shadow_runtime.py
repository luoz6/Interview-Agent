import json
from pathlib import Path

from app.services.in_memory_principal_memory import InMemoryPrincipalMemoryFactStore
from app.services.in_memory_principal_memory_consent import InMemoryPrincipalMemoryConsentStore
from app.services.memory_config import load_effective_memory_config
from scripts.principal_memory_write_shadow import (
    run_fault_matrix, run_write_shadow, validate_artifact,
    validate_write_axis, write_shadow_environment,
)

ROOT = Path(__file__).resolve().parents[1]
RUNBOOK = ROOT / "docs" / "principal-memory-write-shadow-runbook.md"
OBSERVATION = ROOT / "docs" / "principal-memory-write-shadow-observation.json"


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


def test_runbook_requires_proposed_only_and_keeps_read_shadow_blocked():
    text = RUNBOOK.read_text(encoding="utf-8")
    for required in (
        "authority=model_proposed", "final_status=proposed",
        "proposal_created_count=300", "duplicate_fact_count=0",
        "AUTOMATIC_ACTIVE=0", "PRINCIPAL_READ_SHADOW=NOT_RUN",
        "LONG_TERM_MEMORY_CONSUMPTION=BLOCKED",
        "PRODUCTION_OBSERVATION=NOT_RUN",
    ):
        assert required in text
    assert "postgresql://" not in text
    assert "PASS_FOR_PRODUCTION" not in text


def test_committed_observation_binds_clean_revision_and_all_invariants_are_zero():
    record = json.loads(OBSERVATION.read_text(encoding="utf-8"))
    validate_artifact(record)
    assert record["write_shadow_revision"] == "75a21da"
    assert record["sample_count"] == 300
    assert record["proposal_created_count"] == 300
    assert record["proposed_fact_count"] == 300
    assert record["duplicate_fact_count"] == 0
    assert not any(record["hard_invariants"].values())
    assert record["cleanup_residue"] == 0
    assert record["production_observation"] == "NOT_RUN"
