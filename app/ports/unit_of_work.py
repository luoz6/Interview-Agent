from types import TracebackType
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class UnitOfWorkPort(Protocol):
    @property
    def cursor(self) -> Any: ...

    @property
    def state(self) -> str: ...

    def __enter__(self) -> "UnitOfWorkPort": ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...


@runtime_checkable
class UnitOfWorkFactory(Protocol):
    def __call__(self) -> UnitOfWorkPort: ...


__all__ = ["UnitOfWorkFactory", "UnitOfWorkPort"]
