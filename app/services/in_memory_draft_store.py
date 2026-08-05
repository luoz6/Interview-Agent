from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from threading import RLock
from typing import Any, Callable
from uuid import uuid4


class InMemoryDraftStore:
    durability = "memory"

    def __init__(
        self,
        *,
        ttl: timedelta = timedelta(days=7),
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if ttl.total_seconds() <= 0:
            raise ValueError("draft ttl must be positive")
        self._ttl = ttl
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._drafts: dict[str, dict[str, Any]] = {}
        self._lock = RLock()

    def save(
        self,
        *,
        job_description: str,
        resume_text: str,
        job_tags: list[str] | None = None,
        title: str | None = None,
        draft_id: str | None = None,
    ) -> dict[str, Any]:
        _validate_text(job_description, resume_text)
        now = _aware(self._clock())
        resolved_id = draft_id or f"draft_{uuid4()}"
        with self._lock:
            existing = self._drafts.get(resolved_id)
            if existing and _parse_time(existing["expires_at"]) <= now:
                self._drafts.pop(resolved_id, None)
                existing = None
            created_at = existing["created_at"] if existing else now.isoformat()
            expires_at = existing["expires_at"] if existing else (now + self._ttl).isoformat()
            draft = {
                "draft_id": resolved_id,
                "job_description": job_description,
                "resume_text": resume_text,
                "job_tags": list(job_tags or []),
                "title": title,
                "durability": self.durability,
                "created_at": created_at,
                "updated_at": now.isoformat(),
                "expires_at": expires_at,
            }
            self._drafts[resolved_id] = draft
            return deepcopy(draft)

    def get(self, draft_id: str) -> dict[str, Any]:
        with self._lock:
            draft = self._drafts.get(draft_id)
            if draft is None:
                raise ValueError("draft not found")
            if _parse_time(draft["expires_at"]) <= _aware(self._clock()):
                self._drafts.pop(draft_id, None)
                raise ValueError("draft not found")
            return deepcopy(draft)

    def delete(self, draft_id: str) -> bool:
        with self._lock:
            return self._drafts.pop(draft_id, None) is not None

    def clear(self) -> None:
        with self._lock:
            self._drafts.clear()


def _validate_text(job_description: str, resume_text: str) -> None:
    if not job_description or not job_description.strip():
        raise ValueError("job_description is required")
    if not resume_text or not resume_text.strip():
        raise ValueError("resume_text is required")


def _parse_time(value: str) -> datetime:
    return _aware(datetime.fromisoformat(str(value).replace("Z", "+00:00")))


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
