from datetime import timedelta

import pytest

from app.ports.principal_memory import PrincipalMemoryFactStore


def assert_principal_memory_fact_store_contract(
    store,
    *,
    fact,
    now,
    conflict_type,
) -> None:
    assert isinstance(store, PrincipalMemoryFactStore)
    assert store.create_proposal(fact) == fact
    assert store.create_proposal(fact) == fact

    active = store.activate_proposal(
        deployment_id=fact.deployment_id,
        principal_id=fact.principal_id,
        fact_id=fact.fact_id,
        expected_version=1,
        exclusive_key=None,
        now=now,
        expires_at=now + timedelta(days=365),
    )
    assert active.status == "active"
    assert active.user_confirmed is True
    assert store.list_shadow_eligible(
        deployment_id=fact.deployment_id,
        principal_id=fact.principal_id,
        now=now,
        limit=6,
    ) == [active]
    assert store.list_by_principal(
        deployment_id=fact.deployment_id,
        principal_id="principal-contract-other",
        limit=10,
    ) == []

    with pytest.raises(conflict_type, match="version"):
        store.transition(
            deployment_id=fact.deployment_id,
            principal_id=fact.principal_id,
            fact_id=fact.fact_id,
            expected_version=1,
            target_status="revoked",
            now=now,
        )

    assert store.purge_by_session(fact.source_session_id) == 1
    assert store.purge_by_session(fact.source_session_id) == 0
