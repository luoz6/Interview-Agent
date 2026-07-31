import json
from pathlib import Path
from app.services.in_memory_principal_memory import InMemoryPrincipalMemoryFactStore
from app.services.in_memory_principal_memory_consent import InMemoryPrincipalMemoryConsentStore
from scripts.principal_memory_lifecycle_drill import run_lifecycle, run_race_matrix, validate_artifact
ROOT=Path(__file__).resolve().parents[1]
EVIDENCE=ROOT/"docs"/"principal-memory-lifecycle-drill-evidence.json"

def test_complete_lifecycle_purges_fact_and_consent_residue():
    result=run_lifecycle(fact_store=InMemoryPrincipalMemoryFactStore(),consent_store=InMemoryPrincipalMemoryConsentStore()); validate_artifact(result)
    assert result["confirmed_count"]==1
    assert result["superseded_count"]==1
    assert result["rejected_count"]==1
    assert result["selected_before_revoke"]==1
    assert result["selected_after_revoke"]==0
    assert result["session_facts_deleted"]==3
    assert result["fact_residue"]==0 and result["consent_residue"]==0

def test_consent_races_fail_closed():
    result=run_race_matrix()
    assert result=={
        "enqueue_then_revoke_cancelled":1,
        "source_read_then_revoke_cancelled":1,
        "select_then_revoke_excluded":1,
        "revoke_confirm_blocked":1,
        "purge_replay_cancelled":1,
        "unsafe_race_write_count":0,
    }

def test_committed_lifecycle_evidence_binds_clean_revision():
    result=json.loads(EVIDENCE.read_text(encoding="utf-8")); validate_artifact(result)
    assert result["lifecycle_revision"]=="ed37b4f"
    assert result["lifecycle_gate"]=="PASS"
    assert result["consent_race_safety"]=="PASS"
    assert result["fact_residue"]==result["consent_residue"]==result["cleanup_residue"]==0
    assert result["long_term_memory_consumption"]=="BLOCKED"
