from __future__ import annotations

from threading import RLock

from app.services.principal_memory_control import (
    PrincipalMemoryControl,
    PrincipalMemoryControlConflict,
)


class InMemoryPrincipalMemoryControlStore:
    def __init__(self):
        self._items: dict[tuple[str, str, str], PrincipalMemoryControl] = {}
        self._lock = RLock()

    def get_global(self, *, deployment_id: str, principal_id: str):
        with self._lock:
            return self._items.get((deployment_id, principal_id, ""))

    def set_global(
        self,
        *,
        deployment_id: str,
        principal_id: str,
        enabled: bool,
        updated_at,
        expected_version: int | None = None,
    ):
        return self._set(
            deployment_id=deployment_id,
            principal_id=principal_id,
            session_id=None,
            enabled=enabled,
            updated_at=updated_at,
            expected_version=expected_version,
        )

    def get_session(
        self,
        *,
        deployment_id: str,
        principal_id: str,
        session_id: str,
    ):
        with self._lock:
            return self._items.get((deployment_id, principal_id, session_id))

    def set_session(
        self,
        *,
        deployment_id: str,
        principal_id: str,
        session_id: str,
        enabled: bool,
        updated_at,
        expected_version: int | None = None,
    ):
        return self._set(
            deployment_id=deployment_id,
            principal_id=principal_id,
            session_id=session_id,
            enabled=enabled,
            updated_at=updated_at,
            expected_version=expected_version,
        )

    def purge(self, *, deployment_id: str, principal_id: str) -> int:
        with self._lock:
            keys = [
                key
                for key in self._items
                if key[:2] == (deployment_id, principal_id)
            ]
            for key in keys:
                del self._items[key]
            return len(keys)

    def count(self, *, deployment_id: str, principal_id: str) -> int:
        with self._lock:
            return sum(
                key[:2] == (deployment_id, principal_id)
                for key in self._items
            )

    def _set(
        self,
        *,
        deployment_id,
        principal_id,
        session_id,
        enabled,
        updated_at,
        expected_version,
    ):
        key = (deployment_id, principal_id, session_id or "")
        with self._lock:
            current = self._items.get(key)
            current_version = current.version if current is not None else 0
            if (
                expected_version is not None
                and expected_version != current_version
            ):
                raise PrincipalMemoryControlConflict(
                    "principal memory control version changed"
                )
            if current is not None and current.enabled == enabled:
                return current
            stored = PrincipalMemoryControl(
                deployment_id=deployment_id,
                principal_id=principal_id,
                scope="session" if session_id is not None else "global",
                session_id=session_id,
                enabled=enabled,
                updated_at=updated_at,
                version=current_version + 1,
            )
            self._items[key] = stored
            return stored
