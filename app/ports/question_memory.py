from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.services.question_memory_index import QuestionMemoryIndexEntry


@runtime_checkable
class QuestionMemoryIndexStore(Protocol):
    def activate(
        self,
        entry: QuestionMemoryIndexEntry,
    ) -> QuestionMemoryIndexEntry: ...

    def get_active(
        self,
        *,
        session_id: str,
        question_id: str,
        policy_version: str,
    ) -> QuestionMemoryIndexEntry | None: ...

    def list_active(
        self,
        *,
        session_id: str,
        policy_version: str,
        limit: int,
    ) -> list[QuestionMemoryIndexEntry]: ...

    def get_historical(
        self,
        artifact_ref: str,
    ) -> QuestionMemoryIndexEntry | None: ...

    def mark_session_deleted(self, session_id: str) -> int: ...

    def delete_session(self, session_id: str) -> int: ...
