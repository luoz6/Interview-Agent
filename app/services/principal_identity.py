from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class PrincipalIdentity(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    deployment_id: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,128}$")
    principal_id: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,128}$")
    assurance: Literal["test", "trusted_local", "authenticated"]
    resolved_at: datetime


class NullPrincipalIdentityResolver:
    def resolve(self):
        return None


class ExplicitPrincipalIdentityResolver:
    """Identity is constructor-supplied; no request, resume, device, or model inference."""

    def __init__(
        self,
        *,
        deployment_id: str,
        principal_id: str,
        assurance: Literal["test", "trusted_local", "authenticated"] = "test",
        clock=None,
    ) -> None:
        self._identity = PrincipalIdentity(
            deployment_id=deployment_id,
            principal_id=principal_id,
            assurance=assurance,
            resolved_at=(clock or (lambda: datetime.now(timezone.utc)))(),
        )

    def resolve(self):
        return self._identity
