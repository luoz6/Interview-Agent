from datetime import datetime, timedelta, timezone

import pytest

from app.ports.principal_memory import PrincipalMemoryFactStore
from app.services.in_memory_principal_memory import (
    InMemoryPrincipalMemoryFactStore,
    PrincipalMemoryConflict,
)
from app.services.principal_memory_contracts import (
    CONSENT_POLICY_VERSION,
    TAXONOMY_VERSION,
    PrincipalMemoryFact,
    canonical_principal_fact,
    derive_principal_fact_id,
)


NOW = datetime(2026, 7, 30, tzinfo=timezone.utc)


def make_fact(*, principal_id="principal-a", session_id="session-a", value="python"):
    normalized = canonical_principal_fact({"confirmed_skill": value})
    identity = {
        "deployment_id": "single-tenant-local",
        "principal_id": principal_id,
        "fact_type": "confirmed_skill",
        "normalized_fact": normalized,
        "source_manifest_sha256": "a" * 64,
        "source_excerpt_sha256": "b" * 64,
        "consent_policy_version": CONSENT_POLICY_VERSION,
        "taxonomy_version": TAXONOMY_VERSION,
    }
    return PrincipalMemoryFact(
        fact_id=derive_principal_fact_id(**identity),
        **identity,
        confidence=0.9,
        authority="model_proposed",
        source_session_id=session_id,
        created_at=NOW,
    )


def test_proposal_dedup_transition_isolation_and_purge():
    store = InMemoryPrincipalMemoryFactStore()
    fact = make_fact()
    assert store.create_proposal(fact) == fact
    assert store.create_proposal(fact) == fact
    assert isinstance(store, PrincipalMemoryFactStore)

    active = store.transition(
        deployment_id=fact.deployment_id,
        principal_id=fact.principal_id,
        fact_id=fact.fact_id,
        expected_version=1,
        target_status="active",
        now=NOW,
        expires_at=NOW + timedelta(days=365),
    )
    assert active.status == "active"
    assert active.user_confirmed is True
    assert store.list_shadow_eligible(
        deployment_id=fact.deployment_id,
        principal_id=fact.principal_id,
        now=NOW,
        limit=6,
    ) == [active]
    assert store.list_by_principal(
        deployment_id=fact.deployment_id,
        principal_id="principal-b",
        limit=10,
    ) == []

    with pytest.raises(PrincipalMemoryConflict, match="version"):
        store.transition(
            deployment_id=fact.deployment_id,
            principal_id=fact.principal_id,
            fact_id=fact.fact_id,
            expected_version=1,
            target_status="revoked",
            now=NOW,
        )
    assert store.purge_by_session("session-a") == 1
    assert store.purge_by_session("session-a") == 0


def test_expire_batch_is_bounded_and_terminal_facts_do_not_reactivate():
    store = InMemoryPrincipalMemoryFactStore()
    fact = store.create_proposal(make_fact())
    active = store.transition(
        deployment_id=fact.deployment_id,
        principal_id=fact.principal_id,
        fact_id=fact.fact_id,
        expected_version=1,
        target_status="active",
        now=NOW,
        expires_at=NOW,
    )
    assert store.expire_batch(now=NOW, limit=1) == 1
    expired = store.get(
        deployment_id=fact.deployment_id,
        principal_id=fact.principal_id,
        fact_id=fact.fact_id,
    )
    assert expired.status == "expired"
    with pytest.raises(PrincipalMemoryConflict, match="transition"):
        store.transition(
            deployment_id=fact.deployment_id,
            principal_id=fact.principal_id,
            fact_id=fact.fact_id,
            expected_version=expired.version,
            target_status="active",
            now=NOW,
        )
