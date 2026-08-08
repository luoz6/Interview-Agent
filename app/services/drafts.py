"""Revision-aware compatibility facade over the V15 in-memory draft store."""

from copy import deepcopy
from datetime import timedelta
from typing import Any, Callable
from uuid import uuid4

from app.services.in_memory_draft_store import (
    InMemoryDraftStore,
    _aware,
    _parse_time,
    _validate_text,
)

from app.services.interview_plan_revision import PlanSourcePayload, source_payload_sha256


class AnonymousDraftStore(InMemoryDraftStore):
    """Keep V15 expiry/durability while adding immutable-plan bindings."""

    def __init__(
        self,
        *,
        ttl: timedelta = timedelta(days=7),
        clock: Callable | None = None,
    ) -> None:
        super().__init__(ttl=ttl, clock=clock)

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
        draft = self.prepare_save(
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
        return self.commit_save(draft)

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
            if existing and _parse_time(existing["expires_at"]) <= now:
                existing = None
            created_at = existing["created_at"] if existing else now.isoformat()
            expires_at = (
                existing["expires_at"]
                if existing
                else (now + self._ttl).isoformat()
            )
            if clear_plan:
                plan_family_id = None
                latest_plan_revision_id = None
                plan_source_sha256 = None
            elif plan_family_id is None and existing is not None:
                plan_family_id = existing.get("plan_family_id")
                latest_plan_revision_id = existing.get("latest_plan_revision_id")
                plan_source_sha256 = existing.get("plan_source_sha256")

        binding_values = (
            plan_family_id,
            latest_plan_revision_id,
            plan_source_sha256,
        )
        if any(value is not None for value in binding_values) and any(
            value is None for value in binding_values
        ):
            raise ValueError("draft plan binding is incomplete")
        current_source_sha256 = source_payload_sha256(
            PlanSourcePayload(
                job_description=job_description,
                resume_text=resume_text,
                job_tags=job_tags or [],
            )
        )
        plan_status = (
            "no_plan"
            if plan_family_id is None
            else "active"
            if current_source_sha256 == plan_source_sha256
            else "stale"
        )
        draft = {
            "draft_id": resolved_id,
            "job_description": job_description,
            "resume_text": resume_text,
            "job_tags": list(job_tags or []),
            "title": title,
            "plan_family_id": plan_family_id,
            "latest_plan_revision_id": latest_plan_revision_id,
            "plan_source_sha256": plan_source_sha256,
            "plan_status": plan_status,
            "durability": self.durability,
            "created_at": created_at,
            "updated_at": now.isoformat(),
            "expires_at": expires_at,
        }
        return deepcopy(draft)

    def commit_save(self, draft: dict[str, Any]) -> dict[str, Any]:
        committed = deepcopy(draft)
        with self._lock:
            self._drafts[committed["draft_id"]] = committed
        return deepcopy(committed)

    def plan_revision_bindings(self) -> dict[str, str]:
        """Return the committed draft-to-revision bindings used for ref repair."""
        with self._lock:
            now = _aware(self._clock())
            return {
                draft_id: revision_id
                for draft_id, draft in self._drafts.items()
                if _parse_time(draft["expires_at"]) > now
                if (revision_id := draft.get("latest_plan_revision_id")) is not None
            }


__all__ = ["AnonymousDraftStore", "InMemoryDraftStore"]
