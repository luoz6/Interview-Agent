from __future__ import annotations

from app.domain.interview.session_deletion import SessionDeletionJob


class SessionDeletionService:
    def __init__(
        self,
        *,
        session_store,
        job_store,
        tombstone_store=None,
    ) -> None:
        self.session_store = session_store
        self.job_store = job_store
        self.tombstone_store = tombstone_store

    def request(self, session_id: str) -> SessionDeletionJob:
        existing = self.job_store.get_for_session(session_id)
        if existing is not None:
            if self.tombstone_store is not None:
                self.tombstone_store.record_requested(existing)
            return existing
        self.session_store.get(session_id)
        self.session_store.mark_deleting(session_id)
        job = self.job_store.request(session_id)
        if self.tombstone_store is not None:
            self.tombstone_store.record_requested(job)
        return job

    def get(self, session_id: str) -> SessionDeletionJob:
        job = self.job_store.get_for_session(session_id)
        if job is None:
            raise ValueError("deletion job not found")
        return job


__all__ = ["SessionDeletionService"]
