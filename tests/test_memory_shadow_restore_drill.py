import json

import pytest

from scripts.memory_shadow_restore_drill import (
    PRIVATE_RESIDUE_CATEGORIES,
    run_restore_drill,
    validate_evidence_artifact,
)


def test_restore_drill_replays_three_old_snapshots_and_all_fault_boundaries():
    result = run_restore_drill(restore_cycles=3)

    assert result["schema_version"] == "memory-shadow-restore-drill-v1"
    assert result["backup_restore_tombstone_replay"] == "PASS"
    assert result["restore_cycles"] == 3
    assert result["fault_boundaries_exercised"] == 6
    assert result["fault_reclaims_completed"] == 6
    assert result["restored_private_data_residue"] == 0
    assert result["public_knowledge_unchanged"] is True
    assert result["provider_calls"] == 0
    assert result["production_observation"] == "NOT_RUN"
    assert set(result["residue_by_category"]) == set(PRIVATE_RESIDUE_CATEGORIES)
    assert set(result["restored_rows_by_category"]) == set(
        PRIVATE_RESIDUE_CATEGORIES
    )
    assert all(value == 0 for value in result["residue_by_category"].values())
    assert all(
        result["restored_rows_by_category"][key] > 0
        for key in PRIVATE_RESIDUE_CATEGORIES
        if key != "session_bound_consent_bindings"
    )
    assert result["restored_rows_by_category"]["session_bound_consent_bindings"] == 0
    validate_evidence_artifact(result)


def test_restore_drill_evidence_is_aggregate_only():
    result = run_restore_drill(restore_cycles=3)
    rendered = json.dumps(result, sort_keys=True).casefold()

    for blocked in (
        "session_id",
        "principal_id",
        "fact_id",
        "normalized_fact",
        "source_manifest",
        "source_excerpt",
        "artifact_ref",
        "prompt",
        "answer",
        "resume",
        "postgresql://",
        "table_prefix",
        "database_fingerprint",
    ):
        assert blocked not in rendered


def test_restore_drill_requires_at_least_three_cycles():
    with pytest.raises(ValueError, match="at least three"):
        run_restore_drill(restore_cycles=2)


def test_evidence_validator_rejects_private_drill_fields():
    with pytest.raises(RuntimeError, match="private field"):
        validate_evidence_artifact({"session_id": "private"})
