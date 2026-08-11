from __future__ import annotations

from app.adapters.memory.principal_memory import InMemoryPrincipalMemoryFactStore
from app.services.in_memory_principal_memory_consent import (
    InMemoryPrincipalMemoryConsentStore,
)
from app.services.principal_identity import ExplicitPrincipalIdentityResolver
from app.services.principal_memory_deletion import PrincipalMemoryDeletionService
from tests.principal_memory_fixtures import make_fact


def test_principal_and_session_purge_are_conservative_and_idempotent():
    identity = ExplicitPrincipalIdentityResolver(
        deployment_id="single-tenant-local", principal_id="principal-a"
    )
    facts = InMemoryPrincipalMemoryFactStore()
    facts.create_proposal(make_fact())
    deletion = PrincipalMemoryDeletionService(
        identity_resolver=identity,
        consent_store=InMemoryPrincipalMemoryConsentStore(),
        fact_store=facts,
    )
    assert deletion.purge_session("session-a") == 1
    assert deletion.purge_session("session-a") == 0
    assert deletion.purge_current_principal() == {
        "status": "completed",
        "facts_deleted": 0,
        "consents_deleted": 0,
        "controls_deleted": 0,
        "exports_deleted": 0,
        "cache_deleted": 0,
    }
