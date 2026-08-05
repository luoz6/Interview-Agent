from __future__ import annotations

from typing import Any, Protocol


class InterviewLaunchRepository(Protocol):
    def get(self, plan_id: str, command_id: str) -> dict[str, Any] | None: ...

    def mappings_for_session(self, session_id: str) -> list[dict[str, Any]]: ...

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
