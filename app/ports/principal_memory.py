from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable


@runtime_checkable
class PrincipalMemoryFactStore(Protocol):
    def create_proposal(self, fact): ...
    def get(self, *, deployment_id: str, principal_id: str, fact_id: str): ...
    def transition(
        self,
        *,
        deployment_id: str,
        principal_id: str,
        fact_id: str,
        expected_version: int,
        target_status: str,
        now: datetime,
        expires_at: datetime | None = None,
        supersedes_fact_id: str | None = None,
    ): ...
    def list_by_principal(
        self,
        *,
        deployment_id: str,
        principal_id: str,
        limit: int,
        include_terminal: bool = False,
    ): ...
    def list_shadow_eligible(
        self,
        *,
        deployment_id: str,
        principal_id: str,
        now: datetime,
        limit: int,
    ): ...
    def expire_batch(self, *, now: datetime, limit: int) -> int: ...
    def purge_by_session(self, source_session_id: str) -> int: ...
    def purge_by_principal(self, *, deployment_id: str, principal_id: str) -> int: ...
