from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.adapters.memory.principal_memory import InMemoryPrincipalMemoryFactStore
from app.services.principal_memory_safe_refs import (
    InMemoryPrincipalMemorySafeRefStore,
    PrincipalMemorySafeRefInvalid,
)
from tests.principal_memory_fixtures import make_fact


NOW = datetime(2026, 8, 4, tzinfo=timezone.utc)


def test_safe_ref_is_opaque_scoped_versioned_and_short_lived():
    current = {"now": NOW}
    facts = InMemoryPrincipalMemoryFactStore()
    fact = facts.create_proposal(make_fact(principal_id="local-owner"))
    refs = InMemoryPrincipalMemorySafeRefStore(
        clock=lambda: current["now"],
        ref_factory=lambda: "pm-ref-" + "a" * 32,
    )

    safe_ref = refs.issue(fact)

    assert fact.fact_id not in safe_ref
    assert fact.principal_id not in safe_ref
    assert refs.resolve(
        safe_ref,
        deployment_id=fact.deployment_id,
        principal_id=fact.principal_id,
        fact_store=facts,
    ) == fact
    with pytest.raises(PrincipalMemorySafeRefInvalid):
        refs.resolve(
            safe_ref,
            deployment_id=fact.deployment_id,
            principal_id="other-owner",
            fact_store=facts,
        )

    facts.transition(
        deployment_id=fact.deployment_id,
        principal_id=fact.principal_id,
        fact_id=fact.fact_id,
        expected_version=1,
        target_status="rejected",
        now=NOW,
    )
    with pytest.raises(PrincipalMemorySafeRefInvalid):
        refs.resolve(
            safe_ref,
            deployment_id=fact.deployment_id,
            principal_id=fact.principal_id,
            fact_store=facts,
        )

    second = facts.create_proposal(make_fact(value="kafka"))
    second_ref = refs.issue(second)
    current["now"] = NOW + timedelta(minutes=15)
    with pytest.raises(PrincipalMemorySafeRefInvalid):
        refs.resolve(
            second_ref,
            deployment_id=second.deployment_id,
            principal_id=second.principal_id,
            fact_store=facts,
        )
