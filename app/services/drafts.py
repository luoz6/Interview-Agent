from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


class AnonymousDraftStore:
    def __init__(self) -> None:
        self._drafts: dict[str, dict[str, Any]] = {}

    def save(
        self,
        *,
        job_description: str,
        resume_text: str,
        job_tags: list[str] | None = None,
        title: str | None = None,
        draft_id: str | None = None,
    ) -> dict[str, Any]:
        if not job_description or not job_description.strip():
            raise ValueError("job_description is required")
        if not resume_text or not resume_text.strip():
            raise ValueError("resume_text is required")

        now = _now_iso()
        resolved_id = draft_id or f"draft_{uuid4().hex[:12]}"
        existing = self._drafts.get(resolved_id)
        created_at = existing["created_at"] if existing else now
        draft = {
            "draft_id": resolved_id,
            "job_description": job_description,
            "resume_text": resume_text,
            "job_tags": list(job_tags or []),
            "title": title,
            "created_at": created_at,
            "updated_at": now,
        }
        self._drafts[resolved_id] = draft
        return _copy_draft(draft)

    def get(self, draft_id: str) -> dict[str, Any]:
        try:
            return _copy_draft(self._drafts[draft_id])
        except KeyError as exc:
            raise ValueError("draft not found") from exc

    def clear(self) -> None:
        self._drafts.clear()


def _copy_draft(draft: dict[str, Any]) -> dict[str, Any]:
    copied = dict(draft)
    copied["job_tags"] = list(draft["job_tags"])
    return copied


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
