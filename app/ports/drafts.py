from __future__ import annotations

from typing import Any, Protocol


class DraftStore(Protocol):
    def save(
        self,
        *,
        job_description: str,
        resume_text: str,
        job_tags: list[str] | None = None,
        title: str | None = None,
        draft_id: str | None = None,
    ) -> dict[str, Any]: ...

    def get(self, draft_id: str) -> dict[str, Any]: ...

    def delete(self, draft_id: str) -> bool: ...
