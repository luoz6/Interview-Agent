from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from app.adapters.postgres.unit_of_work import PostgresUnitOfWork
from app.ports.interview_entry import InterviewSessionRepository
from app.runtime.interview_workflow import InterviewWorkflowService
from app.domain.interview.prep_plans import launch_plan_from_record
from app.adapters.persistence.postgres.interview_launch_repository import (
    PostgresInterviewLaunchRepository,
)
from app.adapters.persistence.postgres.prep_plan_store import PostgresPrepPlanStore
from app.adapters.persistence.postgres.session_store import (
    PostgresInterviewSessionStore,
)


class PostgresClock:
    def utc_now(self) -> datetime:
        return datetime.now(timezone.utc)


class PostgresIdGenerator:
    def new_session_id(self) -> str:
        return str(uuid4())


class PostgresPrepPlanRepositoryAdapter:
    def __init__(self, store: PostgresPrepPlanStore) -> None:
        self._store = store

    def cleanup(self) -> int:
        return self._store.cleanup()

    def find_editable(self, plan_id: str) -> dict[str, Any] | None:
        with self._store.connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                record = self._store.select_locked(
                    cursor,
                    plan_id,
                    for_update=False,
                )
        if record is None or record.get("state") != "editable":
            return None
        plan, mappings = launch_plan_from_record(record)
        expires_at = record.get("expires_at")
        if isinstance(expires_at, str):
            expires_at = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        return {
            "plan": plan,
            "mappings": mappings,
            "job_description": record.get("job_description"),
            "resume_text": record.get("resume_text"),
            "job_tags": record.get("job_tags", []),
            "state": record.get("state"),
            "public": record.get("public", {}),
            "expires_at": expires_at,
        }

    def consume(
        self,
        *,
        plan_id: str,
        expected_version: int,
        command_id: str,
        session_id: str,
    ) -> dict[str, Any]:
        with self._store.unit_of_work() as unit:  # type: PostgresUnitOfWork
            record = self._store.select_locked(
                unit.cursor,
                plan_id,
                for_update=True,
            )
            self._store.mark_consumed(
                unit.cursor,
                plan_id=plan_id,
                session_id=session_id,
                command_id=command_id,
                consumed_plan_version=expected_version,
            )
            unit.commit()
            return record


class PostgresInterviewLaunchRepositoryAdapter:
    def __init__(self, repository: PostgresInterviewLaunchRepository) -> None:
        self._repository = repository

    def find(self, plan_id: str, command_id: str) -> dict[str, Any] | None:
        return self._repository.get(plan_id, command_id)

    def find_by_plan(self, plan_id: str) -> dict[str, Any] | None:
        return self._repository.get_by_plan(plan_id)

    def create_pending(
        self,
        *,
        plan_id: str,
        command_id: str,
        consumed_plan_version: int,
        session_id: str,
        mappings: list[dict[str, Any]],
    ) -> dict[str, Any]:
        with self._repository._provider.connection() as connection:
            with connection.cursor() as cursor:
                command = self._repository.insert_pending(
                    cursor,
                    plan_id=plan_id,
                    command_id=command_id,
                    consumed_plan_version=consumed_plan_version,
                    session_id=session_id,
                    mappings=mappings,
                )
            connection.commit()
            return command

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


class PostgresInterviewSessionRepositoryAdapter:
    def __init__(self, store: PostgresInterviewSessionStore) -> None:
        self._store = store

    def start(
        self,
        *,
        plan: Any,
        job_description: str,
        resume_text: str,
        job_tags: list[str],
        session_id: str,
    ) -> dict[str, Any]:
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


class PostgresDurableExecutionAdapter:
    def __init__(
        self,
        *,
        workflow_service: InterviewWorkflowService | None,
        session_repository: InterviewSessionRepository,
    ) -> None:
        self._workflow_service = workflow_service
        self._session_repository = session_repository

    def ensure_bootstrapped(self, session_id: str) -> None:
        state = self._session_repository.get(session_id)
        workflow_engine = str(state.get("workflow_engine") or "")
        if not workflow_engine.startswith("langgraph-"):
            return
        if self._workflow_service is None:
            raise RuntimeError("durable workflow service is unavailable")
        self._workflow_service.ensure_interview_bootstrapped(session_id)


__all__ = [
    "PostgresClock",
    "PostgresDurableExecutionAdapter",
    "PostgresIdGenerator",
    "PostgresInterviewLaunchRepositoryAdapter",
    "PostgresInterviewSessionRepositoryAdapter",
    "PostgresPrepPlanRepositoryAdapter",
]
