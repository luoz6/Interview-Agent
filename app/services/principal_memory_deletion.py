from __future__ import annotations


class PrincipalMemoryDeletionService:
    def __init__(self, *, identity_resolver, consent_store, fact_store):
        self.identity_resolver = identity_resolver
        self.consent_store = consent_store
        self.fact_store = fact_store

    def purge_current_principal(self):
        identity = self.identity_resolver.resolve()
        if identity is None:
            raise PermissionError("principal identity is unavailable")
        facts = self.fact_store.purge_by_principal(
            deployment_id=identity.deployment_id,
            principal_id=identity.principal_id,
        )
        consent = self.consent_store.purge(
            deployment_id=identity.deployment_id,
            principal_id=identity.principal_id,
        )
        return {"facts_deleted": facts, "consents_deleted": consent}

    def purge_session(self, session_id: str) -> int:
        return self.fact_store.purge_by_session(session_id)
