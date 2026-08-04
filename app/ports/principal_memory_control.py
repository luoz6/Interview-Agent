from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class PrincipalMemoryControlStore(Protocol):
    def get_global(self, *, deployment_id: str, principal_id: str): ...
    def set_global(
        self,
        *,
        deployment_id: str,
        principal_id: str,
        enabled: bool,
        updated_at,
        expected_version: int | None = None,
    ): ...
    def get_session(
        self,
        *,
        deployment_id: str,
        principal_id: str,
        session_id: str,
    ): ...
    def set_session(
        self,
        *,
        deployment_id: str,
        principal_id: str,
        session_id: str,
        enabled: bool,
        updated_at,
        expected_version: int | None = None,
    ): ...
    def purge(self, *, deployment_id: str, principal_id: str) -> int: ...
    def purge_session(self, session_id: str) -> int: ...
    def count(self, *, deployment_id: str, principal_id: str) -> int: ...
