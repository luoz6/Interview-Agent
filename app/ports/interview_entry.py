from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol


class PrepPlanNotFound(Exception):
    pass


class PrepPlanExpired(Exception):
    pass


class PrepPlanVersionConflict(Exception):
    pass


class PrepPlanAlreadyConsumed(Exception):
    pass


class Clock(Protocol):
    def utc_now(self) -> datetime:
        ...


class IdGenerator(Protocol):
    def new_session_id(self) -> str:
        ...


class PrepPlanRepository(Protocol):
    def cleanup(self) -> int:
        ...

    def find_editable(self, plan_id: str) -> dict[str, Any] | None:
        ...

    def consume(
        self,
        *,
        plan_id: str,
        expected_version: int,
        command_id: str,
        session_id: str,
    ) -> dict[str, Any]:
        ...


class InterviewLaunchRepository(Protocol):
    def find(
        self,
        plan_id: str,
        command_id: str,
    ) -> dict[str, Any] | None:
        ...

    def find_by_plan(self, plan_id: str) -> dict[str, Any] | None:
        ...

    def create_pending(
        self,
        *,
        plan_id: str,
        command_id: str,
        consumed_plan_version: int,
        session_id: str,
        mappings: list[dict[str, Any]],
    ) -> dict[str, Any]:
        ...

    def mark_ready(
        self,
        plan_id: str,
        command_id: str,
    ) -> dict[str, Any]:
        ...

    def mark_failed_recoverable(
        self,
        plan_id: str,
        command_id: str,
        *,
        error_code: str,
        retry_after_seconds: int,
    ) -> dict[str, Any]:
        ...


class InterviewSessionRepository(Protocol):
    def start(
        self,
        *,
        plan: Any,
        job_description: str,
        resume_text: str,
        job_tags: list[str],
        session_id: str,
    ) -> dict[str, Any]:
        ...

    def get(self, session_id: str) -> dict[str, Any]:
        ...

    def delete_session(self, session_id: str) -> None:
        ...


class DurableExecution(Protocol):
    def ensure_bootstrapped(self, session_id: str) -> None:
        ...


__all__ = [
    "Clock",
    "DurableExecution",
    "IdGenerator",
    "InterviewLaunchRepository",
    "InterviewSessionRepository",
    "PrepPlanAlreadyConsumed",
    "PrepPlanExpired",
    "PrepPlanNotFound",
    "PrepPlanRepository",
    "PrepPlanVersionConflict",
]
