from __future__ import annotations

from threading import RLock

from app.services.principal_memory_consent import PrincipalMemoryConsent


class InMemoryPrincipalMemoryConsentStore:
    def __init__(self):
        self._items = {}
        self._lock = RLock()

    def get_current(self, *, deployment_id: str, principal_id: str):
        with self._lock:
            return self._items.get((deployment_id, principal_id))

    def grant(self, consent: PrincipalMemoryConsent):
        with self._lock:
            key = (consent.deployment_id, consent.principal_id)
            current = self._items.get(key)
            version = current.version + 1 if current else 1
            stored = consent.model_copy(update={"version": version, "revoked_at": None})
            self._items[key] = stored
            return stored

    def revoke(self, *, deployment_id: str, principal_id: str, revoked_at):
        with self._lock:
            key = (deployment_id, principal_id)
            current = self._items.get(key)
            if current is None:
                return None
            if current.revoked_at is not None:
                return current
            revoked = current.model_copy(
                update={"revoked_at": revoked_at, "version": current.version + 1}
            )
            self._items[key] = revoked
            return revoked

    def purge(self, *, deployment_id: str, principal_id: str) -> int:
        with self._lock:
            return int(self._items.pop((deployment_id, principal_id), None) is not None)
