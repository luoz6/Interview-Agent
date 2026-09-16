from __future__ import annotations

from app.domain.memory.consent import (
    PRINCIPAL_MEMORY_PURPOSES,
    PrincipalMemoryConsent,
    PrincipalMemoryPurpose,
)


class PrincipalMemoryConsentPolicy:
    def __init__(
        self,
        *,
        identity_resolver,
        store,
        policy_version: str,
        control_service=None,
        deletion_fence=None,
    ):
        self.identity_resolver = identity_resolver
        self.store = store
        self.policy_version = policy_version
        self.control_service = control_service
        self.deletion_fence = deletion_fence

    def authorize(
        self,
        purpose: PrincipalMemoryPurpose,
        *,
        session_id: str | None = None,
    ) -> bool:
        identity = self.identity_resolver.resolve()
        if identity is None:
            return False
        if self.deletion_fence is not None and self.deletion_fence.is_write_blocked(
            deployment_id=identity.deployment_id,
            principal_id=identity.principal_id,
        ):
            return False
        if self.control_service is not None and not self.control_service.allows(
            session_id=session_id
        ):
            return False
        consent = self.store.get_current(
            deployment_id=identity.deployment_id,
            principal_id=identity.principal_id,
        )
        return bool(
            consent
            and consent.policy_version == self.policy_version
            and consent.revoked_at is None
            and purpose in consent.allowed_purposes
        )


PrincipalMemoryConsentService = PrincipalMemoryConsentPolicy


__all__ = [
    "PRINCIPAL_MEMORY_PURPOSES",
    "PrincipalMemoryConsent",
    "PrincipalMemoryConsentPolicy",
    "PrincipalMemoryConsentService",
    "PrincipalMemoryPurpose",
]
