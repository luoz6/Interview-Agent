from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PrincipalMemoryControlConflict(RuntimeError):
    """Raised when a stale control version attempts to overwrite newer intent."""


class PrincipalMemoryControl(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["principal-memory-control-v1"] = (
        "principal-memory-control-v1"
    )
    deployment_id: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,128}$")
    principal_id: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,128}$")
    scope: Literal["global", "session"]
    session_id: str | None = Field(default=None, min_length=1, max_length=128)
    enabled: bool
    updated_at: datetime
    version: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def validate_scope(self):
        if (self.scope == "global") != (self.session_id is None):
            raise ValueError("principal memory control scope conflicts with session")
        if self.updated_at.tzinfo is None:
            raise ValueError("principal memory control timestamp must be timezone-aware")
        return self


class PrincipalMemoryControlService:
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
            "global_enabled": (
                global_control is None or global_control.enabled
            ),
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
