from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.ports.interview_entry import InterviewSessionRepository
from app.adapters.memory.interview_launch_repository import (
    InMemoryInterviewLaunchRepository,
)
from app.adapters.memory.prep_plan_store import InMemoryPrepPlanStore
from app.domain.interview.prep_plans import PrepPlanError, launch_plan_from_record


class MemoryClock:
    def utc_now(self) -> datetime:
        return datetime.now(timezone.utc)


class MemoryIdGenerator:
    def new_session_id(self) -> str:
        from uuid import uuid4

        return str(uuid4())


class MemoryPrepPlanRepositoryAdapter:
    def __init__(self, store: InMemoryPrepPlanStore) -> None:
        self._store = store

    def cleanup(self) -> int:
        return self._store.cleanup()

    def find_editable(self, plan_id: str) -> dict[str, Any] | None:
        try:
            with self._store.transaction(plan_id) as record:
                if record.get("state") != "editable":
                    return None
                plan, mappings = launch_plan_from_record(record)
                return {
                    "plan": plan,
                    "mappings": mappings,
                    "job_description": record.get("job_description"),
                    "resume_text": record.get("resume_text"),
                    "job_tags": record.get("job_tags", []),
                    "state": record.get("state"),
                    "public": record.get("public", {}),
                    "expires_at": datetime.fromisoformat(
                        record.get("expires_at", "")
                    ),
                }
        except (PrepPlanError, ValueError):
            return None

    def consume(
        self,
        *,
        plan_id: str,
        expected_version: int,
        command_id: str,
        session_id: str,
    ) -> dict[str, Any]:
        with self._store.transaction(plan_id) as record:
            record["state"] = "consumed"
            record["public"]["state"] = "consumed"
            record["consumed_session_id"] = session_id
            record["consumed_command_id"] = command_id
            record["consumed_plan_version"] = expected_version
            record["updated_at"] = datetime.now(timezone.utc).isoformat()
            return dict(record)


class MemoryInterviewLaunchRepositoryAdapter:
    def __init__(self, repository: InMemoryInterviewLaunchRepository) -> None:
        self._repository = repository

    def find(self, plan_id: str, command_id: str) -> dict[str, Any] | None:
        return self._repository.get(plan_id, command_id)

    def find_by_plan(self, plan_id: str) -> dict[str, Any] | None:
        return self._repository.get_by_plan(plan_id)

    def create_pending(self, **kwargs: Any) -> dict[str, Any]:
        return self._repository.create_pending(**kwargs)

    def mark_ready(self, plan_id: str, command_id: str) -> dict[str, Any]:
        return self._repository.mark_ready(plan_id, command_id)

    def mark_failed_recoverable(
        self,
        plan_id: str,
        command_id: str,
        *,
        error_code: str,
        retry_after_seconds: int,
    ) -> dict[str, Any]:
        return self._repository.mark_failed_recoverable(
            plan_id,
            command_id,
            error_code=error_code,
            retry_after_seconds=retry_after_seconds,
        )


class MemoryInterviewSessionRepositoryAdapter:
    def __init__(self, store, scheduler_entry=None) -> None:
        self._store = store
        self._scheduler_entry = scheduler_entry

    def start(
        self,
        *,
        plan: Any,
        job_description: str,
        resume_text: str,
        job_tags: list[str],
        session_id: str,
    ) -> dict[str, Any]:
        if self._scheduler_entry is not None:
            self._scheduler_entry.start(
                plan,
                job_description=job_description,
                resume_text=resume_text,
                job_tags=job_tags,
                session_id=session_id,
            )
        else:
            self._store.start(
                plan,
                job_description=job_description,
                resume_text=resume_text,
                job_tags=job_tags,
                session_id=session_id,
            )
        return {"session_id": session_id}

    def get(self, session_id: str) -> dict[str, Any]:
        return self._store.get(session_id)

    def delete_session(self, session_id: str) -> None:
        self._store.delete_session(session_id)


class MemoryDurableExecutionAdapter:
    def ensure_bootstrapped(self, session_id: str) -> None:
        return None


__all__ = [
    "MemoryClock",
    "MemoryDurableExecutionAdapter",
    "MemoryIdGenerator",
    "MemoryInterviewLaunchRepositoryAdapter",
    "MemoryInterviewSessionRepositoryAdapter",
    "MemoryPrepPlanRepositoryAdapter",
]
