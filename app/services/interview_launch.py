from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from app.graphs.interview_state import choose_workflow_engine
from app.services.in_memory_interview_launch_repository import (
    InMemoryInterviewLaunchRepository,
)
from app.services.in_memory_prep_plan_store import InMemoryPrepPlanStore
from app.services.postgres_interview_launch_repository import (
    PostgresInterviewLaunchRepository,
)
from app.services.postgres_prep_plan_store import PostgresPrepPlanStore
from app.services.postgres_session import PostgresInterviewSessionStore
from app.services.prep_plans import (
    PrepPlanError,
    launch_plan_from_record,
    plan_expired,
)


class InterviewLaunchCoordinator:
    def __init__(
        self,
        *,
        prep_plan_store,
        session_store,
        launch_repository,
        workflow_service=None,
    ) -> None:
        self.prep_plan_store = prep_plan_store
        self.session_store = session_store
        self.launch_repository = launch_repository
        self.workflow_service = workflow_service

    def launch(
        self,
        *,
        plan_id: str,
        expected_plan_version: int,
        command_id: str,
    ) -> dict[str, Any]:
        _validate_command_id(command_id)
        if isinstance(self.prep_plan_store, PostgresPrepPlanStore):
            return self._launch_postgres(
                plan_id=plan_id,
                expected_plan_version=expected_plan_version,
                command_id=command_id,
            )
        return self._launch_memory(
            plan_id=plan_id,
            expected_plan_version=expected_plan_version,
            command_id=command_id,
        )

    def _launch_memory(
        self,
        *,
        plan_id: str,
        expected_plan_version: int,
        command_id: str,
    ) -> dict[str, Any]:
        repository: InMemoryInterviewLaunchRepository = self.launch_repository
        self.prep_plan_store.cleanup()
        fast_path = repository.get_by_plan(plan_id)
        if fast_path is not None:
            if fast_path["command_id"] != command_id:
                raise self._already_consumed(fast_path)
            return self._bootstrap_and_respond(fast_path, replayed=True)
        created_session_id: str | None = None
        with self.prep_plan_store.transaction(plan_id) as record:
            existing = repository.get_by_plan(plan_id)
            if existing:
                if existing["command_id"] != command_id:
                    raise self._already_consumed(existing)
                command = existing
            else:
                self._validate_record_for_launch(
                    record,
                    plan_id=plan_id,
                    expected_plan_version=expected_plan_version,
                )
                plan, mappings = launch_plan_from_record(record)
                session_id = str(uuid4())
                repository_snapshot = repository.snapshot()
                try:
                    self.session_store.start(
                        plan,
                        job_description=record["job_description"],
                        resume_text=record["resume_text"],
                        job_tags=record["job_tags"],
                        session_id=session_id,
                    )
                    created_session_id = session_id
                    command = repository.create_pending(
                        plan_id=plan_id,
                        command_id=command_id,
                        consumed_plan_version=expected_plan_version,
                        session_id=session_id,
                        mappings=mappings,
                    )
                    record["state"] = "consumed"
                    record["public"]["state"] = "consumed"
                    record["consumed_session_id"] = session_id
                    record["consumed_command_id"] = command_id
                    record["consumed_plan_version"] = expected_plan_version
                    record["updated_at"] = datetime.now(timezone.utc).isoformat()
                except BaseException:
                    if created_session_id:
                        self.session_store.delete_session(created_session_id)
                    repository.restore(repository_snapshot)
                    raise
        return self._bootstrap_and_respond(command, replayed=created_session_id is None)

    def _launch_postgres(
        self,
        *,
        plan_id: str,
        expected_plan_version: int,
        command_id: str,
    ) -> dict[str, Any]:
        store: PostgresPrepPlanStore = self.prep_plan_store
        repository: PostgresInterviewLaunchRepository = self.launch_repository
        session_store: PostgresInterviewSessionStore = self.session_store

        store.cleanup()

        fast_path = repository.get(plan_id, command_id)
        if fast_path is not None:
            return self._bootstrap_and_respond(fast_path, replayed=True)

        with store.connection_provider.connection() as connection:
            try:
                with connection.cursor() as cursor:
                    try:
                        record = store.select_locked(cursor, plan_id, for_update=True)
                    except PrepPlanError as exc:
                        if exc.code != "PREP_PLAN_NOT_FOUND":
                            raise
                        # The PrepPlan payload may have been removed after its
                        # retention window while the launch command remains as
                        # the durable idempotency tombstone. Reuse the current
                        # transaction instead of opening a nested connection,
                        # and defer bootstrap until this connection is closed.
                        existing = repository.select_by_plan(cursor, plan_id)
                        if existing is None:
                            raise
                        if existing["command_id"] != command_id:
                            raise self._already_consumed(existing)
                        command = existing
                        replayed = True
                    else:
                        # Mandatory lock-internal second lookup. This closes the
                        # race where concurrent requests both miss the fast path.
                        existing = repository.select_by_plan(cursor, plan_id)
                        if existing:
                            if existing["command_id"] != command_id:
                                raise self._already_consumed(existing)
                            command = existing
                            replayed = True
                        else:
                            self._validate_record_for_launch(
                                record,
                                plan_id=plan_id,
                                expected_plan_version=expected_plan_version,
                            )
                            plan, mappings = launch_plan_from_record(record)
                            session_id = str(uuid4())
                            graph_version, memory_policy_version = self._engine_for_session(session_id)
                            session_store.insert_session_in_transaction(
                                cursor,
                                session_id=session_id,
                                plan=plan,
                                job_description=record["job_description"],
                                resume_text=record["resume_text"],
                                job_tags=record["job_tags"],
                                graph_version=graph_version,
                                memory_policy_version=memory_policy_version,
                            )
                            command = repository.insert_pending(
                                cursor,
                                plan_id=plan_id,
                                command_id=command_id,
                                consumed_plan_version=expected_plan_version,
                                session_id=session_id,
                                mappings=mappings,
                            )
                            store.mark_consumed(
                                cursor,
                                plan_id=plan_id,
                                session_id=session_id,
                                command_id=command_id,
                                consumed_plan_version=expected_plan_version,
                            )
                            replayed = False
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return self._bootstrap_and_respond(command, replayed=replayed)

    def _engine_for_session(self, session_id: str) -> tuple[str, str]:
        workflow = self.workflow_service
        if workflow is None:
            return "legacy", "deterministic-v1"
        engine = choose_workflow_engine(
            session_id,
            runtime_store=workflow.runtime_store,
            runtime_enabled=workflow.runtime_enabled,
            rollout_percent=workflow.rollout_percent,
            durable_version=workflow.default_graph_version,
        )
        return engine, workflow.memory_policy_resolver(engine)

    def _bootstrap_and_respond(
        self,
        command: dict[str, Any],
        *,
        replayed: bool,
    ) -> dict[str, Any]:
        if command["bootstrap_status"] == "ready":
            return self._response(command, replayed=replayed)
        state = self.session_store.get(command["session_id"])
        durable = str(state.get("workflow_engine") or "").startswith("langgraph-")
        if not durable:
            command = self.launch_repository.mark_ready(
                command["plan_id"], command["command_id"]
            )
            return self._response(command, replayed=replayed)
        try:
            self.workflow_service.ensure_interview_bootstrapped(command["session_id"])
        except Exception as exc:
            retry_after = min(5, max(1, int(command.get("bootstrap_attempt_count", 0)) + 1))
            command = self.launch_repository.mark_failed_recoverable(
                command["plan_id"],
                command["command_id"],
                error_code="INTERVIEW_BOOTSTRAP_FAILED",
                retry_after_seconds=retry_after,
            )
            raise PrepPlanError(
                "INTERVIEW_BOOTSTRAP_PENDING",
                "面试会话已创建，正在恢复初始化，请稍后继续。",
                status_code=503,
                retryable=True,
                details={
                    "session_id": command["session_id"],
                    "retry_after_seconds": retry_after,
                },
            ) from exc
        command = self.launch_repository.mark_ready(
            command["plan_id"], command["command_id"]
        )
        return self._response(command, replayed=replayed)

    def _response(self, command: dict[str, Any], *, replayed: bool) -> dict[str, Any]:
        state = self.session_store.get(command["session_id"])
        current = None
        if state.get("status") != "finished" and state["plan"].questions:
            index = min(state.get("current_index", 0), len(state["plan"].questions) - 1)
            current = state["plan"].questions[index].model_dump(mode="json")
        session = {
            "session_id": command["session_id"],
            "status": state.get("status", "active"),
            "current_question": current,
        }
        return {
            "session_id": command["session_id"],
            "command_id": command["command_id"],
            "status": state.get("status", "active"),
            "current_question": current,
            "bootstrap_status": command["bootstrap_status"],
            "replayed": replayed,
            "session": session,
        }

    @staticmethod
    def _validate_record_for_launch(
        record: dict[str, Any],
        *,
        plan_id: str,
        expected_plan_version: int,
    ) -> None:
        expires_at = datetime.fromisoformat(record["expires_at"].replace("Z", "+00:00"))
        if expires_at <= datetime.now(timezone.utc):
            raise plan_expired(plan_id)
        if record["state"] != "editable":
            raise PrepPlanError(
                "PREP_PLAN_ALREADY_CONSUMED",
                "计划已经用于创建面试。",
                status_code=409,
                details={"session_id": record.get("consumed_session_id")},
            )
        latest = int(record["public"]["plan_version"])
        if expected_plan_version != latest:
            raise PrepPlanError(
                "PREP_PLAN_VERSION_CONFLICT",
                "计划已经更新，请确认最新版本。",
                status_code=409,
                retryable=True,
                details={"plan_id": plan_id, "latest_version": latest},
            )

    @staticmethod
    def _already_consumed(command: dict[str, Any]) -> PrepPlanError:
        return PrepPlanError(
            "PREP_PLAN_ALREADY_CONSUMED",
            "计划已经用于创建面试，请继续已创建的会话。",
            status_code=409,
            details={
                "session_id": command["session_id"],
                "bootstrap_status": command["bootstrap_status"],
            },
        )


def _validate_command_id(command_id: str) -> None:
    raw = str(command_id or "").strip()
    candidate = raw.rsplit("_", 1)[-1]
    try:
        parsed = UUID(candidate)
    except (ValueError, AttributeError) as exc:
        raise PrepPlanError(
            "INVALID_COMMAND_ID",
            "启动标识无效，请重新发起操作。",
            status_code=422,
        ) from exc
    if parsed.version != 4:
        raise PrepPlanError(
            "INVALID_COMMAND_ID",
            "启动标识必须使用安全的 UUIDv4。",
            status_code=422,
        )
