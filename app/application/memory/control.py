from __future__ import annotations

from datetime import datetime, timezone

from app.domain.memory.control import (
    PrincipalMemoryControl,
    PrincipalMemoryControlConflict,
)


class PrincipalMemoryControlPolicy:
    def __init__(self, *, identity_resolver, store, clock=None):
        self.identity_resolver = identity_resolver
        self.store = store
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def allows(self, *, session_id: str | None = None) -> bool:
        identity = self.identity_resolver.resolve()
        if identity is None:
            return False
        global_control = self.store.get_global(
            deployment_id=identity.deployment_id,
            principal_id=identity.principal_id,
        )
        if global_control is not None and not global_control.enabled:
            return False
        if session_id is None:
            return True
        session_control = self.store.get_session(
            deployment_id=identity.deployment_id,
            principal_id=identity.principal_id,
            session_id=session_id,
        )
        return session_control is None or session_control.enabled

    def set_global_enabled(
        self,
        enabled: bool,
        *,
        expected_version: int | None = None,
    ) -> PrincipalMemoryControl:
        identity = self._identity()
        return self.store.set_global(
            deployment_id=identity.deployment_id,
            principal_id=identity.principal_id,
            enabled=enabled,
            updated_at=self.clock(),
            expected_version=expected_version,
        )

    def set_session_ignored(
        self,
        session_id: str,
        ignored: bool,
        *,
        expected_version: int | None = None,
    ) -> PrincipalMemoryControl:
        identity = self._identity()
        if not session_id or len(session_id) > 128:
            raise ValueError("session_id must be a bounded non-empty identifier")
        return self.store.set_session(
            deployment_id=identity.deployment_id,
            principal_id=identity.principal_id,
            session_id=session_id,
            enabled=not ignored,
            updated_at=self.clock(),
            expected_version=expected_version,
        )

    def snapshot(self, *, session_id: str | None = None) -> dict:
        identity = self._identity()
        global_control = self.store.get_global(
            deployment_id=identity.deployment_id,
            principal_id=identity.principal_id,
        )
        session_control = (
            self.store.get_session(
                deployment_id=identity.deployment_id,
                principal_id=identity.principal_id,
                session_id=session_id,
            )
            if session_id is not None
            else None
        )
        return {
            "global_enabled": global_control is None or global_control.enabled,
            "global_version": global_control.version if global_control else 0,
            "session_ignored": bool(
                session_control is not None and not session_control.enabled
            ),
            "session_version": session_control.version if session_control else 0,
        }

    def _identity(self):
        identity = self.identity_resolver.resolve()
        if identity is None:
            raise PermissionError("principal identity is unavailable")
        return identity


PrincipalMemoryControlService = PrincipalMemoryControlPolicy


__all__ = [
    "PrincipalMemoryControl",
    "PrincipalMemoryControlConflict",
    "PrincipalMemoryControlPolicy",
    "PrincipalMemoryControlService",
]
