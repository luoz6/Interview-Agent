from __future__ import annotations

from typing import Any
from uuid import UUID

from app.ports.interview_entry import (
    Clock,
    DurableExecution,
    IdGenerator,
    InterviewLaunchRepository,
    InterviewSessionRepository,
    PrepPlanAlreadyConsumed,
    PrepPlanExpired,
    PrepPlanNotFound,
    PrepPlanRepository,
    PrepPlanVersionConflict,
)


class LaunchPreparedInterview:
    """Canonical application use case for prepared-plan interview launch."""

    def __init__(
        self,
        *,
        prep_plan_repository: PrepPlanRepository,
        launch_repository: InterviewLaunchRepository,
        session_repository: InterviewSessionRepository,
        durable_execution: DurableExecution,
        clock: Clock,
        id_generator: IdGenerator,
    ) -> None:
        self.prep_plan_repository = prep_plan_repository
        self.launch_repository = launch_repository
        self.session_repository = session_repository
        self.durable_execution = durable_execution
        self.clock = clock
        self.id_generator = id_generator

    def launch(
        self,
        *,
        plan_id: str,
        expected_plan_version: int,
        command_id: str,
    ) -> dict[str, Any]:
        self._validate_command_id(command_id)

        existing = self.launch_repository.find(plan_id, command_id)
        if existing is not None:
            return self._bootstrap_and_respond(existing, replayed=True)

        if self.launch_repository.find_by_plan(plan_id) is not None:
            raise PrepPlanAlreadyConsumed("prep plan already consumed")

        record = self.prep_plan_repository.find_editable(plan_id)
        if record is None:
            raise PrepPlanNotFound("prep plan not found")

        self._validate_record_for_launch(
            record,
            plan_id=plan_id,
            expected_plan_version=expected_plan_version,
        )

        session_id = self.id_generator.new_session_id()
        self.session_repository.start(
            plan=record["plan"],
            job_description=record["job_description"],
            resume_text=record["resume_text"],
            job_tags=record["job_tags"],
            session_id=session_id,
        )

        try:
            command = self.launch_repository.create_pending(
                plan_id=plan_id,
                command_id=command_id,
                consumed_plan_version=expected_plan_version,
                session_id=session_id,
                mappings=record.get("mappings", []),
            )
            self.prep_plan_repository.consume(
                plan_id=plan_id,
                expected_version=expected_plan_version,
                command_id=command_id,
                session_id=session_id,
            )
        except BaseException:
            self.session_repository.delete_session(session_id)
            raise

        return self._bootstrap_and_respond(command, replayed=False)

    def _validate_command_id(self, command_id: str) -> None:
        raw = str(command_id or "").strip()
        try:
            candidate = raw.rsplit("_", 1)[-1]
            parsed = UUID(candidate)
        except (ValueError, AttributeError) as exc:
            raise ValueError("command_id must contain UUIDv4") from exc
        if parsed.version != 4:
            raise ValueError("command_id must use UUIDv4")

    def _validate_record_for_launch(
        self,
        record: dict[str, Any],
        *,
        plan_id: str,
        expected_plan_version: int,
    ) -> None:
        if record.get("state") != "editable":
            raise PrepPlanAlreadyConsumed("prep plan already consumed")

        expires_at = record.get("expires_at")
        if expires_at is not None and expires_at <= self.clock.utc_now():
            raise PrepPlanExpired("prep plan expired")

        latest = int(record.get("public", {}).get("plan_version", 0))
        if expected_plan_version != latest:
            raise PrepPlanVersionConflict("prep plan version conflict")

    def _bootstrap_and_respond(
        self,
        command: dict[str, Any],
        *,
        replayed: bool,
    ) -> dict[str, Any]:
        if command.get("bootstrap_status") == "ready":
            return self._response(command, replayed=replayed)

        session_id = command["session_id"]
        try:
            self.durable_execution.ensure_bootstrapped(session_id)
        except Exception:
            retry_after = min(
                5,
                max(1, int(command.get("bootstrap_attempt_count", 0)) + 1),
            )
            command = self.launch_repository.mark_failed_recoverable(
                command["plan_id"],
                command["command_id"],
                error_code="INTERVIEW_BOOTSTRAP_FAILED",
                retry_after_seconds=retry_after,
            )
            raise

        command = self.launch_repository.mark_ready(
            command["plan_id"],
            command["command_id"],
        )
        return self._response(command, replayed=replayed)

    def _response(
        self,
        command: dict[str, Any],
        *,
        replayed: bool,
    ) -> dict[str, Any]:
        session_id = command["session_id"]
        state = self.session_repository.get(session_id)
        current = None
        plan = state.get("plan") or {}
        if hasattr(plan, "get"):
            questions = plan.get("questions") or []
        else:
            questions = getattr(plan, "questions", None) or []
        if state.get("status") != "finished" and questions:
            index = min(state.get("current_index", 0), len(questions) - 1)
            current = questions[index]

        session = {
            "session_id": session_id,
            "status": state.get("status", "active"),
            "current_question": current,
        }
        return {
            "session_id": session_id,
            "command_id": command["command_id"],
            "status": state.get("status", "active"),
            "current_question": current,
            "bootstrap_status": command.get("bootstrap_status"),
            "replayed": replayed,
            "session": session,
        }


__all__ = ["LaunchPreparedInterview"]
