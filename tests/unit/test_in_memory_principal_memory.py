from datetime import timedelta

import pytest

from app.adapters.memory.principal_memory import (
    InMemoryPrincipalMemoryFactStore,
    PrincipalMemoryConflict,
    transition_fact,
)
from tests.principal_memory_fixtures import FACT_NOW as NOW, make_fact
from tests.principal_memory_store_contract import (
    assert_principal_memory_fact_store_contract,
)


def test_proposal_dedup_transition_isolation_and_purge():
    assert_principal_memory_fact_store_contract(
        InMemoryPrincipalMemoryFactStore(),
        fact=make_fact(),
        now=NOW,
        conflict_type=PrincipalMemoryConflict,
    )


def test_expire_batch_is_bounded_and_terminal_facts_do_not_reactivate():
    store = InMemoryPrincipalMemoryFactStore()
    fact = store.create_proposal(make_fact())
    active = store.activate_proposal(
        deployment_id=fact.deployment_id,
        principal_id=fact.principal_id,
        fact_id=fact.fact_id,
        expected_version=1,
        exclusive_key=None,
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
        store.activate_proposal(
            deployment_id=fact.deployment_id,
            principal_id=fact.principal_id,
            fact_id=fact.fact_id,
            expected_version=expired.version,
            exclusive_key=None,
            now=NOW,
            expires_at=NOW + timedelta(days=365),
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


def test_in_memory_store_rejects_forged_taxonomy_scope_key():
    store = InMemoryPrincipalMemoryFactStore()
    fact = make_fact().model_copy(
        update={
            "authority": "user_declared",
            "status": "active",
            "user_confirmed": True,
            "confirmed_at": NOW,
        }
    )

    with pytest.raises(ValueError, match="store-owned taxonomy"):
        store.declare_active(
            fact,
            exclusive_key="interview_language",
            now=NOW,
        )

    assert store.declare_active(
        fact,
        exclusive_key=None,
        now=NOW,
    ).status == "active"


def test_generic_transition_cannot_bypass_atomic_activation():
    store = InMemoryPrincipalMemoryFactStore()
    fact = store.create_proposal(make_fact())

    with pytest.raises(ValueError, match="activate_proposal"):
        store.transition(
            deployment_id=fact.deployment_id,
            principal_id=fact.principal_id,
            fact_id=fact.fact_id,
            expected_version=1,
            target_status="active",
            now=NOW,
            expires_at=NOW + timedelta(days=365),
        )
