from __future__ import annotations

from datetime import datetime, timezone
from threading import RLock
from typing import Callable

from app.services.question_memory_index import QuestionMemoryIndexEntry


class InMemoryQuestionMemoryIndexStore:
    def __init__(
        self,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._lock = RLock()
        self._entries: dict[str, QuestionMemoryIndexEntry] = {}
        self._deleted_sessions: set[str] = set()

    def activate(self, entry: QuestionMemoryIndexEntry) -> QuestionMemoryIndexEntry:
        if entry.status != "active":
            raise ValueError("new question memory index entry must be active")
        key = (entry.session_id, entry.question_id, entry.policy_version)
        with self._lock:
            if entry.session_id in self._deleted_sessions:
                raise ValueError("question memory session is deleted")
            existing_same_ref = self._entries.get(entry.artifact_ref)
            if existing_same_ref is not None:
                if existing_same_ref == entry:
                    return existing_same_ref
                raise ValueError("question memory artifact ref conflicts")
            now = self._clock()
            previous = next(
                (
                    item
                    for item in self._entries.values()
                    if (
                        item.session_id,
                        item.question_id,
                        item.policy_version,
                    )
                    == key
                    and item.status == "active"
                ),
                None,
            )
            if previous is not None:
                self._entries[previous.artifact_ref] = previous.model_copy(
                    update={"status": "superseded", "superseded_at": now}
                )
            activated = entry.model_copy(
                update={
                    "supersedes_artifact_ref": (
                        previous.artifact_ref if previous is not None else None
                    )
                }
            )
            self._entries[activated.artifact_ref] = activated
            return activated

    def get_active(self, *, session_id, question_id, policy_version):
        with self._lock:
            if session_id in self._deleted_sessions:
                return None
            return next(
                (
                    item
                    for item in self._entries.values()
                    if item.session_id == session_id
                    and item.question_id == question_id
                    and item.policy_version == policy_version
                    and item.status == "active"
                ),
                None,
            )

    def list_active(self, *, session_id, policy_version, limit):
        if limit <= 0:
            raise ValueError("question memory list limit must be positive")
        with self._lock:
            if session_id in self._deleted_sessions:
                return []
            items = [
                item
                for item in self._entries.values()
                if item.session_id == session_id
                and item.policy_version == policy_version
                and item.status == "active"
            ]
            items.sort(
                key=lambda item: (
                    item.source_max_sequence_no,
                    item.created_at,
                ),
                reverse=True,
            )
            return items[:limit]

    def get_historical(self, artifact_ref):
        with self._lock:
            return self._entries.get(artifact_ref)

    def mark_session_deleted(self, session_id):
        with self._lock:
            self._deleted_sessions.add(session_id)
            now = self._clock()
            count = 0
            for ref, item in list(self._entries.items()):
                if item.session_id != session_id or item.status == "deleted":
                    continue
                self._entries[ref] = item.model_copy(
                    update={"status": "deleted", "deleted_at": now}
                )
                count += 1
            return count

    def delete_session(self, session_id):
        with self._lock:
            refs = [
                ref
                for ref, item in self._entries.items()
                if item.session_id == session_id
            ]
            for ref in refs:
                del self._entries[ref]
            self._deleted_sessions.add(session_id)
            return len(refs)
