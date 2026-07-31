from __future__ import annotations

from typing import Protocol

from app.services.session_deletion import SessionDeletionJob


class SessionDeletionJobStore(Protocol):
    def request(self, session_id: str) -> SessionDeletionJob: ...

    def get_for_session(self, session_id: str) -> SessionDeletionJob | None: ...

    def claim(self, *, worker_id: str, lease_seconds: int) -> SessionDeletionJob | None: ...

    def complete(self, job: SessionDeletionJob, *, safe_counts: dict[str, int]) -> SessionDeletionJob: ...

    def fail(self, job: SessionDeletionJob, *, error_code: str) -> SessionDeletionJob: ...
