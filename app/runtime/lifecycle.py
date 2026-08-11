from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol


class RuntimeResourceResolver(Protocol):
    def get(self, key: str, default: object | None = None) -> object | None: ...


@dataclass(frozen=True)
class RuntimeCloser:
    key: str
    close: Callable[[object, bool], None]


def close_runtime_resources(
    resolver: RuntimeResourceResolver,
    closers: tuple[RuntimeCloser, ...],
    *,
    wait: bool,
) -> None:
    """Close registered resources in order and attempt every cleanup."""

    errors: list[Exception] = []
    for closer in closers:
        resource = resolver.get(closer.key)
        if resource is None:
            continue
        try:
            closer.close(resource, wait)
        except Exception as exc:
            errors.append(exc)
    if errors:
        raise ExceptionGroup("runtime resource shutdown failed", errors)


def shutdown_with_optional_wait(resource: object, wait: bool) -> None:
    shutdown = getattr(resource, "shutdown", None)
    if shutdown is not None:
        shutdown(wait=wait)


def shutdown_without_wait_argument(resource: object, _wait: bool) -> None:
    shutdown = getattr(resource, "shutdown", None)
    if shutdown is not None:
        shutdown()


def close_without_wait_argument(resource: object, _wait: bool) -> None:
    close = getattr(resource, "close", None)
    if close is not None:
        close()
