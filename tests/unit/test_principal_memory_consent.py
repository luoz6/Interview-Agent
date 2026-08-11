from __future__ import annotations

from datetime import datetime, timezone

from app.ports.principal_memory_consent import PrincipalMemoryConsentStore
from app.services.in_memory_principal_memory_consent import (
    InMemoryPrincipalMemoryConsentStore,
)
from app.services.principal_identity import ExplicitPrincipalIdentityResolver
from app.services.principal_memory_consent import (
    PrincipalMemoryConsent,
    PrincipalMemoryConsentService,
)


def test_consent_is_checked_at_operation_time_and_revocation_is_immediate():
    now = datetime(2026, 7, 30, tzinfo=timezone.utc)
    identity = ExplicitPrincipalIdentityResolver(
        deployment_id="single-tenant-local",
        principal_id="principal-consent",
    )
    store = InMemoryPrincipalMemoryConsentStore()
    service = PrincipalMemoryConsentService(
        identity_resolver=identity,
        store=store,
        policy_version="principal-memory-consent-v1",
    )
    assert service.authorize("proposal_write") is False
    store.grant(
        PrincipalMemoryConsent(
            deployment_id="single-tenant-local",
            principal_id="principal-consent",
            policy_version="principal-memory-consent-v1",
            allowed_purposes=[
                "proposal_write",
                "fact_storage",
                "read_shadow",
                "local_consume",
            ],
            granted_at=now,
        )
    )
    assert service.authorize("proposal_write") is True
    assert service.authorize("local_consume") is True

    store.revoke(
        deployment_id="single-tenant-local",
        principal_id="principal-consent",
        revoked_at=now,
    )
    assert service.authorize("proposal_write") is False
    assert service.authorize("read_shadow") is False
    assert isinstance(store, PrincipalMemoryConsentStore)
