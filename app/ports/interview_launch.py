from __future__ import annotations

from typing import Any, Protocol

from app.ports.unit_of_work import UnitOfWorkPort


class InterviewLaunchRepository(Protocol):
    def get(self, plan_id: str, command_id: str) -> dict[str, Any] | None: ...

    def mappings_for_session(self, session_id: str) -> list[dict[str, Any]]: ...

    def get_by_plan(self, plan_id: str) -> dict[str, Any] | None: ...

    def create_pending(
        self,
        *,
        plan_id: str,
        command_id: str,
        consumed_plan_version: int,
        session_id: str,
        mappings: list[dict[str, Any]],
    ) -> dict[str, Any]: ...

    def mark_ready(self, plan_id: str, command_id: str) -> dict[str, Any]: ...


class TransactionalPrepPlanStore(Protocol):
    durability: str

    def cleanup(self) -> int: ...

    def unit_of_work(self) -> UnitOfWorkPort: ...

    def select_locked(
        self,
        cursor,
        plan_id: str,
        *,
        for_update: bool = True,
    ) -> dict[str, Any]: ...

    def mark_consumed(self, cursor, **kwargs: Any) -> None: ...


class TransactionalInterviewLaunchRepository(InterviewLaunchRepository, Protocol):
    def select_by_plan(self, cursor, plan_id: str) -> dict[str, Any] | None: ...

    def insert_pending(self, cursor, **kwargs: Any) -> dict[str, Any]: ...


class TransactionalSessionRepository(Protocol):
    def insert_session_in_transaction(self, cursor, **kwargs: Any) -> Any: ...


__all__ = [
    "InterviewLaunchRepository",
    "TransactionalInterviewLaunchRepository",
    "TransactionalPrepPlanStore",
    "TransactionalSessionRepository",
]
