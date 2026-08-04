from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


PrincipalMemoryPurpose = Literal[
    "proposal_write",
    "fact_storage",
    "read_shadow",
    "local_consume",
]


class PrincipalMemoryConsent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["principal-memory-consent-v1"] = (
        "principal-memory-consent-v1"
    )
    deployment_id: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,128}$")
    principal_id: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,128}$")
    policy_version: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,128}$")
    allowed_purposes: list[PrincipalMemoryPurpose] = Field(min_length=1)
    granted_at: datetime
    revoked_at: datetime | None = None
    version: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def validate_consent(self):
        if len(self.allowed_purposes) != len(set(self.allowed_purposes)):
            raise ValueError("consent purposes cannot be duplicated")
        if self.granted_at.tzinfo is None or (
            self.revoked_at is not None and self.revoked_at.tzinfo is None
        ):
            raise ValueError("consent timestamps must be timezone-aware")
        return self


class PrincipalMemoryConsentService:
    def __init__(
        self,
        *,
        identity_resolver,
        store,
        policy_version: str,
        control_service=None,
    ):
        self.identity_resolver = identity_resolver
        self.store = store
        self.policy_version = policy_version
        self.control_service = control_service

    def authorize(
        self,
        purpose: PrincipalMemoryPurpose,
        *,
        session_id: str | None = None,
    ) -> bool:
        identity = self.identity_resolver.resolve()
        if identity is None:
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
