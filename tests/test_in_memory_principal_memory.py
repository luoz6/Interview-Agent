from datetime import datetime, timedelta, timezone

import pytest

from app.ports.principal_memory import PrincipalMemoryFactStore
from app.services.in_memory_principal_memory import (
    InMemoryPrincipalMemoryFactStore,
    PrincipalMemoryConflict,
    transition_fact,
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


@pytest.mark.parametrize(
    ("source_status", "target_status"),
    [
        ("proposed", "active"),
        ("proposed", "rejected"),
        ("proposed", "expired"),
        ("proposed", "deleted"),
        ("active", "superseded"),
        ("active", "expired"),
        ("active", "revoked"),
        ("active", "deleted"),
    ],
)
def test_complete_fact_transition_matrix(source_status, target_status):
    fact = make_fact()
    if source_status == "active":
        fact = transition_fact(
            fact,
            expected_version=1,
            target_status="active",
            now=NOW,
            expires_at=NOW + timedelta(days=180),
        )
    updated = transition_fact(
        fact,
        expected_version=fact.version,
        target_status=target_status,
        now=NOW,
        expires_at=(
            NOW + timedelta(days=180) if target_status == "active" else None
        ),
    )
    assert updated.status == target_status


@pytest.mark.parametrize(
    "terminal",
    ["rejected", "superseded", "expired", "revoked", "deleted"],
)
def test_terminal_fact_statuses_have_no_outgoing_transition(terminal):
    fact = make_fact().model_copy(
        update={
            "status": terminal,
            "revoked_at": NOW if terminal == "revoked" else None,
            "deleted_at": NOW if terminal == "deleted" else None,
        }
    )
    with pytest.raises(PrincipalMemoryConflict, match="transition"):
        transition_fact(
            fact,
            expected_version=fact.version,
            target_status="active",
            now=NOW,
        )


def test_proposal_retention_boundary_is_inclusive_and_batch_is_bounded():
    store = InMemoryPrincipalMemoryFactStore()
    cutoff = NOW - timedelta(days=7)
    due = make_fact().model_copy(update={"created_at": cutoff})
    fresh = make_fact(value="kafka").model_copy(
        update={"created_at": cutoff + timedelta(microseconds=1)}
    )
    store.create_proposal(due)
    store.create_proposal(fresh)

    assert store.expire_batch(
        now=NOW,
        proposal_created_before=cutoff,
        limit=1,
    ) == 1
    assert store.get(
        deployment_id=due.deployment_id,
        principal_id=due.principal_id,
        fact_id=due.fact_id,
    ).status == "expired"
    assert store.get(
        deployment_id=fresh.deployment_id,
        principal_id=fresh.principal_id,
        fact_id=fresh.fact_id,
    ).status == "proposed"
