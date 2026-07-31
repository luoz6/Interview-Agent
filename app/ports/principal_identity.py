from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class PrincipalIdentityResolver(Protocol):
    def resolve(self): ...
