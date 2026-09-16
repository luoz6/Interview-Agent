from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from threading import RLock
from typing import Any


class InMemoryInterviewLaunchRepository:
    def __init__(self) -> None:
        self._commands: dict[str, dict[str, Any]] = {}
        self._mappings: dict[str, list[dict[str, Any]]] = {}
        self._lock = RLock()

    def get(self, plan_id: str, command_id: str) -> dict[str, Any] | None:
        with self._lock:
            command = self._commands.get(plan_id)
            if command is None or command["command_id"] != command_id:
                return None
            return deepcopy(command)

    def get_by_plan(self, plan_id: str) -> dict[str, Any] | None:
        with self._lock:
            command = self._commands.get(plan_id)
            return deepcopy(command) if command else None

    def create_pending(
        self,
        *,
        plan_id: str,
        command_id: str,
        consumed_plan_version: int,
        session_id: str,
        mappings: list[dict[str, Any]],
    ) -> dict[str, Any]:
        with self._lock:
            existing = self._commands.get(plan_id)
            if existing:
                if existing["command_id"] == command_id:
                    return deepcopy(existing)
                raise ValueError("plan launch command already exists")
            now = datetime.now(timezone.utc).isoformat()
            command = {
                "plan_id": plan_id,
                "command_id": command_id,
                "consumed_plan_version": consumed_plan_version,
                "session_id": session_id,
                "bootstrap_status": "bootstrap_pending",
                "bootstrap_attempt_count": 0,
                "last_bootstrap_attempt_at": None,
                "next_retry_at": None,
                "last_error_code": None,
                "last_error_retryable": True,
                "created_at": now,
                "updated_at": now,
            }
            self._commands[plan_id] = command
            self._mappings[session_id] = deepcopy(mappings)
            return deepcopy(command)

    def mark_ready(self, plan_id: str, command_id: str) -> dict[str, Any]:
        with self._lock:
            command = self._require(plan_id, command_id)
            command["bootstrap_status"] = "ready"
            command["bootstrap_attempt_count"] += 1
            command["last_bootstrap_attempt_at"] = datetime.now(timezone.utc).isoformat()
            command["next_retry_at"] = None
            command["last_error_code"] = None
            command["updated_at"] = command["last_bootstrap_attempt_at"]
            return deepcopy(command)

    def mark_failed_recoverable(
        self,
        plan_id: str,
        command_id: str,
        *,
        error_code: str,
        retry_after_seconds: int,
    ) -> dict[str, Any]:
        from datetime import timedelta

        with self._lock:
            command = self._require(plan_id, command_id)
            now = datetime.now(timezone.utc)
            command["bootstrap_status"] = "failed_recoverable"
            command["bootstrap_attempt_count"] += 1
            command["last_bootstrap_attempt_at"] = now.isoformat()
            command["next_retry_at"] = (now + timedelta(seconds=retry_after_seconds)).isoformat()
            command["last_error_code"] = error_code
            command["last_error_retryable"] = True
            command["updated_at"] = now.isoformat()
            return deepcopy(command)

    def mappings_for_session(self, session_id: str) -> list[dict[str, Any]]:
        with self._lock:
            return deepcopy(self._mappings.get(session_id, []))

    def snapshot(self) -> tuple[dict[str, Any], dict[str, Any]]:
        with self._lock:
            return deepcopy(self._commands), deepcopy(self._mappings)

    def restore(self, snapshot: tuple[dict[str, Any], dict[str, Any]]) -> None:
        with self._lock:
            self._commands, self._mappings = deepcopy(snapshot)

    def _require(self, plan_id: str, command_id: str) -> dict[str, Any]:
        command = self._commands.get(plan_id)
        if command is None or command["command_id"] != command_id:
            raise ValueError("launch command not found")
        return command

    def clear(self) -> None:
        with self._lock:
            self._commands.clear()
            self._mappings.clear()
