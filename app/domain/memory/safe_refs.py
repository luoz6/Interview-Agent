from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


class PrincipalMemorySafeRefInvalid(ValueError):
    pass


class PrincipalMemorySafeRefVersionConflict(PrincipalMemorySafeRefInvalid):
    pass


@dataclass(frozen=True)
class PrincipalMemorySafeRefRecord:
    safe_ref: str
    deployment_id: str
    principal_id: str
    fact_id: str
    fact_version: int
    expires_at: datetime


__all__ = [
    "PrincipalMemorySafeRefInvalid",
    "PrincipalMemorySafeRefRecord",
    "PrincipalMemorySafeRefVersionConflict",
]
