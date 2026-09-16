from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.application.interview.launch_prepared_interview import (
    LaunchPreparedInterview,
)
from app.ports.interview_entry import (
    PrepPlanAlreadyConsumed,
    PrepPlanExpired,
    PrepPlanNotFound,
    PrepPlanVersionConflict,
)


class Clock:
    def __init__(self, now: datetime | None = None) -> None:
        self.now = now or datetime.now(timezone.utc)

    def utc_now(self) -> datetime:
        return self.now


class IdGenerator:
    def __init__(self) -> None:
        self.next_id = 1

    def new_session_id(self) -> str:
        value = f"session-{self.next_id}"
        self.next_id += 1
        return value


class PrepPlanRepository:
    def __init__(self, record: dict | None) -> None:
        self.record = record

    def cleanup(self) -> int:
        return 0

    def find_editable(self, plan_id: str) -> dict | None:
        if self.record is None:
            return None
        return dict(self.record)

    def consume(self, **kwargs) -> dict:
        self.record["state"] = "consumed"
        return dict(self.record)


class InterviewLaunchRepository:
    def __init__(self) -> None:
        self.commands: dict[str, dict] = {}

    def find(self, plan_id: str, command_id: str) -> dict | None:
        command = self.commands.get(plan_id)
        if command and command["command_id"] == command_id:
            return dict(command)
        return None

    def find_by_plan(self, plan_id: str) -> dict | None:
        return dict(self.commands.get(plan_id, {})) or None

    def create_pending(self, **kwargs) -> dict:
        command = {
            **kwargs,
            "bootstrap_status": "pending",
            "bootstrap_attempt_count": 0,
        }
        self.commands[kwargs["plan_id"]] = command
        return command

    def mark_ready(self, plan_id: str, command_id: str) -> dict:
        command = self.commands[plan_id]
        command["bootstrap_status"] = "ready"
        return command

    def mark_failed_recoverable(self, *args, **kwargs) -> dict:
        command = self.commands[args[0]]
        command["bootstrap_status"] = "failed_recoverable"
        return command


class InterviewSessionRepository:
    def __init__(self) -> None:
        self.sessions: dict[str, dict] = {}

    def start(self, **kwargs) -> dict:
        session_id = kwargs["session_id"]
        self.sessions[session_id] = {
            "status": "active",
            "current_index": 0,
            "workflow_engine": "legacy",
            "plan": {"questions": [{"id": "q1"}]},
        }
        return {"session_id": session_id}

    def get(self, session_id: str) -> dict:
        return self.sessions[session_id]

    def delete_session(self, session_id: str) -> None:
        self.sessions.pop(session_id, None)


class DurableExecution:
    def __init__(self, error: Exception | None = None) -> None:
        self.calls = 0
        self.error = error

    def ensure_bootstrapped(self, session_id: str) -> None:
        self.calls += 1
        if self.error is not None:
            raise self.error


def _record(*, version: int = 1, state: str = "editable") -> dict:
    return {
        "plan": {"questions": []},
        "mappings": [],
        "job_description": "backend",
        "resume_text": "backend",
        "job_tags": ["backend"],
        "state": state,
        "public": {"plan_version": version},
        "expires_at": datetime.now(timezone.utc) + timedelta(hours=1),
    }


def _use_case(record: dict | None) -> tuple[LaunchPreparedInterview, InterviewLaunchRepository]:
    launch_repo = InterviewLaunchRepository()
    uc = LaunchPreparedInterview(
        prep_plan_repository=PrepPlanRepository(record),
        launch_repository=launch_repo,
        session_repository=InterviewSessionRepository(),
        durable_execution=DurableExecution(),
        clock=Clock(),
        id_generator=IdGenerator(),
    )
    return uc, launch_repo


def _command_id() -> str:
    return f"cmd_{uuid4()}"


def test_normal_path_returns_ready():
    uc, _ = _use_case(_record())
    result = uc.launch(
        plan_id="p1",
        expected_plan_version=1,
        command_id=_command_id(),
    )
    assert result["bootstrap_status"] == "ready"
    assert result["session_id"].startswith("session-")


def test_invalid_plan_raises_not_found():
    uc, _ = _use_case(None)
    with pytest.raises(PrepPlanNotFound):
        uc.launch(
            plan_id="missing",
            expected_plan_version=1,
            command_id=_command_id(),
        )


def test_expired_plan_raises():
    record = _record()
    record["expires_at"] = datetime.now(timezone.utc) - timedelta(seconds=1)
    uc, _ = _use_case(record)
    with pytest.raises(PrepPlanExpired):
        uc.launch(
            plan_id="p1",
            expected_plan_version=1,
            command_id=_command_id(),
        )


def test_version_mismatch_raises():
    uc, _ = _use_case(_record(version=2))
    with pytest.raises(PrepPlanVersionConflict):
        uc.launch(
            plan_id="p1",
            expected_plan_version=1,
            command_id=_command_id(),
        )


def test_same_command_replays():
    uc, launch_repo = _use_case(_record())
    command_id = _command_id()
    first = uc.launch(
        plan_id="p1",
        expected_plan_version=1,
        command_id=command_id,
    )
    second = uc.launch(
        plan_id="p1",
        expected_plan_version=1,
        command_id=command_id,
    )
    assert first["session_id"] == second["session_id"]
    assert second["replayed"] is True
    assert launch_repo.commands["p1"]["command_id"] == command_id


def test_same_plan_different_command_raises():
    uc, _ = _use_case(_record())
    uc.launch(
        plan_id="p1",
        expected_plan_version=1,
        command_id=_command_id(),
    )
    with pytest.raises(PrepPlanAlreadyConsumed):
        uc.launch(
            plan_id="p1",
            expected_plan_version=1,
            command_id=_command_id(),
        )


def test_bootstrap_success_marks_ready():
    uc, launch_repo = _use_case(_record())
    durable = DurableExecution()
    uc.durable_execution = durable
    result = uc.launch(
        plan_id="p1",
        expected_plan_version=1,
        command_id=_command_id(),
    )
    assert result["bootstrap_status"] == "ready"
    assert durable.calls == 1


def test_bootstrap_recoverable_failure_marks_failed_and_raises():
    uc, launch_repo = _use_case(_record())
    uc.durable_execution = DurableExecution(RuntimeError("bootstrap failed"))
    with pytest.raises(RuntimeError):
        uc.launch(
            plan_id="p1",
            expected_plan_version=1,
            command_id=_command_id(),
        )
    command = launch_repo.commands["p1"]
    assert command["bootstrap_status"] == "failed_recoverable"
