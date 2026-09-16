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
PRINCIPAL_MEMORY_PURPOSES = frozenset(
    {"proposal_write", "fact_storage", "read_shadow", "local_consume"}
)


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


__all__ = [
    "PRINCIPAL_MEMORY_PURPOSES",
    "PrincipalMemoryConsent",
    "PrincipalMemoryPurpose",
]
