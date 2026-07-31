from app.services.in_memory_principal_memory import InMemoryPrincipalMemoryFactStore
from app.services.in_memory_principal_memory_consent import InMemoryPrincipalMemoryConsentStore
from scripts.principal_memory_lifecycle_drill import run_lifecycle, run_race_matrix, validate_artifact

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
