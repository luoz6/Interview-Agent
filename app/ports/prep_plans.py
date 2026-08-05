from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Any, Protocol

from app.services.prep import InterviewPlan


class PrepPlanStore(Protocol):
    durability: str

    def create(
        self,
        *,
        plan: InterviewPlan,
        job_description: str,
        resume_text: str,
        job_tags: list[str],
        source_draft_id: str | None = None,
    ) -> dict[str, Any]: ...

    def get(self, plan_id: str) -> dict[str, Any]: ...

    def apply_operations(
        self,
        plan_id: str,
        *,
        expected_version: int,
        operations: list[dict[str, Any]],
    ) -> dict[str, Any]: ...

    def delete_by_source_draft(self, draft_id: str) -> int: ...

    def cleanup(self) -> int: ...

    def transaction(self, plan_id: str) -> AbstractContextManager[Any]: ...
