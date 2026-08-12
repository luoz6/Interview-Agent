from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal


class PrincipalMemorySessionChoiceConflict(RuntimeError):
    """The deterministic launch identity is already bound to another choice."""


class PrincipalMemorySessionChoiceBinder:
    """Apply the launch-time memory choice before a business session can run."""

    def __init__(self, *, identity_resolver, control_store, clock=None):
        self.identity_resolver = identity_resolver
        self.control_store = control_store
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def prepare(
        self,
        *,
        session_id: str,
        mode: Literal["inherit", "ignore"],
    ) -> bool:
        if mode != "ignore" or self.identity_resolver is None or self.control_store is None:
            return False
        identity = self.identity_resolver.resolve()
        if identity is None or identity.assurance != "trusted_local":
            return False
        current = self.control_store.get_session(
            deployment_id=identity.deployment_id,
            principal_id=identity.principal_id,
            session_id=session_id,
        )
        if current is not None:
            if current.enabled:
                raise PrincipalMemorySessionChoiceConflict()
            return False
        self.control_store.set_session(
            deployment_id=identity.deployment_id,
            principal_id=identity.principal_id,
            session_id=session_id,
            enabled=False,
            updated_at=self.clock(),
            expected_version=0,
        )
        return True

    def rollback(self, *, session_id: str, created: bool) -> None:
        if created:
            self.control_store.purge_session(session_id)
