import json
from app.services.in_memory_principal_memory import InMemoryPrincipalMemoryFactStore
from app.services.in_memory_principal_memory_consent import InMemoryPrincipalMemoryConsentStore
from app.services.memory_config import load_effective_memory_config
from scripts.principal_memory_read_shadow import read_shadow_environment, run_read_shadow, validate_artifact, validate_read_axis


def test_read_shadow_is_single_axis_and_bounded():
    config=load_effective_memory_config(read_shadow_environment())
    assert validate_read_axis(config)==[]
    assert config.long_term.max_shadow_facts==3
    assert config.long_term.max_shadow_tokens==200


def test_300_sample_read_shadow_is_zero_injection():
    record=run_read_shadow(fact_store=InMemoryPrincipalMemoryFactStore(),consent_store=InMemoryPrincipalMemoryConsentStore(),sample_count=300)
    validate_artifact(record)
    assert record["sample_count"]==300
    assert record["would_select_count"]>0
    assert record["conflict_count"]>0
    assert not any(record["hard_invariants"].values())
    assert record["latency_regression_ratio"]<=.2
    assert record["digest_values_persisted"] is False
    assert record["long_term_memory_consumption"]=="BLOCKED"
    assert "principal_id" not in json.dumps(record)
