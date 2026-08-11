from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from threading import RLock
from typing import Any, TypeVar, cast

from app.runtime.config import EffectiveRuntimeConfig, load_effective_runtime_config


T = TypeVar("T")
_MISSING = object()


@dataclass(frozen=True)
class RuntimeContainerSnapshot:
    state: str
    instance_keys: tuple[str, ...]
    flag_keys: tuple[str, ...]
    metadata_keys: tuple[str, ...]
    config_loaded: bool


class RuntimeContainer:
    """Own runtime configuration, singleton instances, and lifecycle state."""

    def __init__(
        self,
        *,
        config: EffectiveRuntimeConfig | None = None,
        config_loader: Callable[[], EffectiveRuntimeConfig] = (
            load_effective_runtime_config
        ),
    ) -> None:
        self._config = config
        self._config_loader = config_loader
        self._instances: dict[str, Any] = {}
        self._flags: dict[str, bool] = {}
        self._metadata: dict[str, Any] = {}
        self._state = "new"
        self._lock = RLock()
        self._lifecycle_lock = RLock()

    @property
    def lifecycle_lock(self) -> RLock:
        return self._lifecycle_lock

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    @property
    def config(self) -> EffectiveRuntimeConfig:
        with self._lock:
            self._ensure_mutable()
            if self._config is None:
                self._config = self._config_loader()
            return self._config

    def get(self, key: str, default: T | None = None) -> T | None:
        with self._lock:
            return cast(T | None, self._instances.get(key, default))

    def require(self, key: str) -> Any:
        with self._lock:
            value = self._instances.get(key, _MISSING)
            if value is _MISSING:
                raise KeyError(f"runtime dependency is not registered: {key}")
            return value

    def set(self, key: str, value: T) -> T:
        with self._lock:
            self._ensure_mutable()
            self._instances[key] = value
            return value

    def get_or_create(self, key: str, factory: Callable[[], T]) -> T:
        with self._lock:
            self._ensure_mutable()
            value = self._instances.get(key, _MISSING)
            if value is _MISSING:
                value = factory()
                self._instances[key] = value
            return cast(T, value)

    def remove(self, key: str) -> Any | None:
        with self._lock:
            return self._instances.pop(key, None)

    def flag(self, key: str) -> bool:
        with self._lock:
            return self._flags.get(key, False)

    def set_flag(self, key: str, value: bool) -> None:
        with self._lock:
            self._ensure_mutable()
            self._flags[key] = bool(value)

    def metadata(self, key: str, factory: Callable[[], T]) -> T:
        with self._lock:
            self._ensure_mutable()
            if key not in self._metadata:
                self._metadata[key] = factory()
            return cast(T, self._metadata[key])

    def mark_open(self) -> None:
        with self._lock:
            if self._state in {"closing", "closed"}:
                raise RuntimeError(
                    f"{self._state} RuntimeContainer cannot be reopened"
                )
            self._state = "open"

    def begin_close(self) -> bool:
        with self._lock:
            if self._state in {"closing", "closed"}:
                return False
            self._state = "closing"
            return True

    def finish_close(self) -> None:
        with self._lock:
            self._instances.clear()
            self._flags.clear()
            self._metadata.clear()
            self._config = None
            self._state = "closed"

    def snapshot(self) -> RuntimeContainerSnapshot:
        with self._lock:
            return RuntimeContainerSnapshot(
                state=self._state,
                instance_keys=tuple(sorted(self._instances)),
                flag_keys=tuple(sorted(self._flags)),
                metadata_keys=tuple(sorted(self._metadata)),
                config_loaded=self._config is not None,
            )

    def _ensure_mutable(self) -> None:
        if self._state == "closed":
            raise RuntimeError(
                f"{self._state} RuntimeContainer cannot create or replace state"
            )


def build_runtime_container(
    *,
    environ: Mapping[str, str] | None = None,
) -> RuntimeContainer:
    if environ is None:
        return RuntimeContainer()
    config = load_effective_runtime_config(environ)
    return RuntimeContainer(config=config)
