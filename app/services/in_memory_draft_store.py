from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from threading import RLock
from typing import Any, Callable
from uuid import uuid4

from app.domain.interview.drafts import (
    DraftWriteConflict,
    plan_status,
    validate_plan_binding,
)
from app.services.interview_plan_revision import (
    PlanSourcePayload,
    source_payload_sha256,
)


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
        self._draft_epochs: dict[str, int] = {}
        self._lock = RLock()

    def save(
        self,
        *,
        job_description: str,
        resume_text: str,
        job_tags: list[str] | None = None,
        title: str | None = None,
        draft_id: str | None = None,
        plan_family_id: str | None = None,
        latest_plan_revision_id: str | None = None,
        plan_source_sha256: str | None = None,
        clear_plan: bool = False,
    ) -> dict[str, Any]:
        prepared = self.prepare_save(
            job_description=job_description,
            resume_text=resume_text,
            job_tags=job_tags,
            title=title,
            draft_id=draft_id,
            plan_family_id=plan_family_id,
            latest_plan_revision_id=latest_plan_revision_id,
            plan_source_sha256=plan_source_sha256,
            clear_plan=clear_plan,
        )
        return self.commit_save(prepared)

    def prepare_save(
        self,
        *,
        job_description: str,
        resume_text: str,
        job_tags: list[str] | None = None,
        title: str | None = None,
        draft_id: str | None = None,
        plan_family_id: str | None = None,
        latest_plan_revision_id: str | None = None,
        plan_source_sha256: str | None = None,
        clear_plan: bool = False,
    ) -> dict[str, Any]:
        _validate_text(job_description, resume_text)
        now = _aware(self._clock())
        resolved_id = draft_id or f"draft_{uuid4()}"
        with self._lock:
            existing = self._drafts.get(resolved_id)
            active = bool(
                existing and _parse_time(existing["expires_at"]) > now
            )
            current_epoch = self._draft_epochs.get(resolved_id, 0)
            if existing is not None:
                current_epoch = max(
                    current_epoch,
                    int(existing["draft_version"]),
                )
            created_at = existing["created_at"] if active else now.isoformat()
            expires_at = (
                existing["expires_at"]
                if active
                else (now + self._ttl).isoformat()
            )
            if clear_plan:
                plan_family_id = None
                latest_plan_revision_id = None
                plan_source_sha256 = None
            elif plan_family_id is None and active:
                plan_family_id = existing.get("plan_family_id")
                latest_plan_revision_id = existing.get(
                    "latest_plan_revision_id"
                )
                plan_source_sha256 = existing.get("plan_source_sha256")

        validate_plan_binding(
            plan_family_id,
            latest_plan_revision_id,
            plan_source_sha256,
        )
        resolved_tags = list(job_tags or [])
        current_source_sha256 = source_payload_sha256(
            PlanSourcePayload(
                job_description=job_description,
                resume_text=resume_text,
                job_tags=resolved_tags,
            )
        )
        draft = {
            "draft_id": resolved_id,
            "job_description": job_description,
            "resume_text": resume_text,
            "job_tags": resolved_tags,
            "title": title,
            "plan_family_id": plan_family_id,
            "latest_plan_revision_id": latest_plan_revision_id,
            "plan_source_sha256": plan_source_sha256,
            "plan_status": plan_status(
                current_source_sha256=current_source_sha256,
                plan_family_id=plan_family_id,
                plan_source_sha256=plan_source_sha256,
            ),
            "draft_version": current_epoch + 1,
            "durability": self.durability,
            "created_at": created_at,
            "updated_at": now.isoformat(),
            "expires_at": expires_at,
            "_expected_draft_epoch": current_epoch,
            "_expected_updated_at": existing["updated_at"] if existing else None,
            "_expected_row_state": (
                "active" if active else "inactive" if existing else "missing"
            ),
        }
        return deepcopy(draft)

    def commit_save(self, draft: dict[str, Any]) -> dict[str, Any]:
        committed = deepcopy(draft)
        expected_epoch = int(committed.pop("_expected_draft_epoch"))
        expected_updated_at = committed.pop("_expected_updated_at")
        expected_state = committed.pop("_expected_row_state")
        with self._lock:
            current = self._drafts.get(committed["draft_id"])
            current_epoch = self._draft_epochs.get(committed["draft_id"], 0)
            if current is not None:
                current_epoch = max(
                    current_epoch,
                    int(current["draft_version"]),
                )
            current_active = bool(
                current
                and _parse_time(current["expires_at"]) > _aware(self._clock())
            )
            current_state = (
                "active"
                if current_active
                else "inactive"
                if current is not None
                else "missing"
            )
            if (
                current_epoch != expected_epoch
                or current_state != expected_state
                or (
                    current is not None
                    and current.get("updated_at") != expected_updated_at
                )
            ):
                raise DraftWriteConflict("draft changed after it was prepared")
            self._drafts[committed["draft_id"]] = committed
            self._draft_epochs[committed["draft_id"]] = int(
                committed["draft_version"]
            )
        return deepcopy(committed)

    def get(self, draft_id: str) -> dict[str, Any]:
        with self._lock:
            draft = self._drafts.get(draft_id)
            if draft is None:
                raise ValueError("draft not found")
            if _parse_time(draft["expires_at"]) <= _aware(self._clock()):
                self._drafts.pop(draft_id, None)
                self._draft_epochs[draft_id] = max(
                    self._draft_epochs.get(draft_id, 0),
                    int(draft["draft_version"]),
                ) + 1
                raise ValueError("draft not found")
            return deepcopy(draft)

    def delete(self, draft_id: str) -> bool:
        with self._lock:
            draft = self._drafts.pop(draft_id, None)
            if draft is None:
                return False
            self._draft_epochs[draft_id] = max(
                self._draft_epochs.get(draft_id, 0),
                int(draft["draft_version"]),
            ) + 1
            return True

    def clear(self) -> None:
        with self._lock:
            for draft_id, draft in self._drafts.items():
                self._draft_epochs[draft_id] = max(
                    self._draft_epochs.get(draft_id, 0),
                    int(draft["draft_version"]),
                ) + 1
            self._drafts.clear()

    def plan_revision_bindings(self) -> dict[str, str]:
        with self._lock:
            now = _aware(self._clock())
            return {
                draft_id: revision_id
                for draft_id, draft in self._drafts.items()
                if _parse_time(draft["expires_at"]) > now
                if (revision_id := draft.get("latest_plan_revision_id"))
                is not None
            }


def _validate_text(job_description: str, resume_text: str) -> None:
    if not job_description or not job_description.strip():
        raise ValueError("job_description is required")
    if not resume_text or not resume_text.strip():
        raise ValueError("resume_text is required")


def _parse_time(value: str) -> datetime:
    return _aware(datetime.fromisoformat(str(value).replace("Z", "+00:00")))


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
