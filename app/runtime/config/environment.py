from __future__ import annotations

import os
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar


_environment_override: ContextVar[Mapping[str, str] | None] = ContextVar(
    "runtime_environment_override",
    default=None,
)


def process_environment() -> Mapping[str, str]:
    """Return the active read-only environment source.

    Production uses the process environment. Tests and config loaders may install a
    scoped mapping without mutating process-global state.
    """

    override = _environment_override.get()
    return os.environ if override is None else override


def environment_value(name: str, default: str | None = None) -> str | None:
    return process_environment().get(name, default)


@contextmanager
def use_environment(environ: Mapping[str, str]) -> Iterator[None]:
    """Temporarily resolve configuration from ``environ`` in the current context."""

    normalized = {str(key): str(value) for key, value in environ.items()}
    token = _environment_override.set(normalized)
    try:
        yield
    finally:
        _environment_override.reset(token)


def set_default_environment_value(name: str, value: str) -> None:
    """Set a process default for a library that only supports environment config."""

    if _environment_override.get() is not None:
        return
    os.environ.setdefault(name, value)
