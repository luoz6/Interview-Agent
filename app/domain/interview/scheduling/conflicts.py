from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domain.interview.scheduling.commands import UserCommand
from app.domain.interview.scheduling.waits import WaitHandle


CommandDisposition = Literal["ACCEPT", "REPLAY", "CONFLICT", "REJECT", "STALE"]


class UserCommandConflictOutcome(BaseModel):
    """Deterministic result of applying the UserCommand conflict matrix."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    command_id: str = Field(min_length=1)
    disposition: CommandDisposition
    reason_code: str = Field(min_length=1)

    @property
    def accepted(self) -> bool:
        return self.disposition in {"ACCEPT", "REPLAY"}

    @property
    def idempotent_replay(self) -> bool:
        return self.disposition == "REPLAY"


def classify_user_command(
    command: UserCommand,
    *,
    wait_handle: WaitHandle,
    current_revision: int,
    recorded_command: UserCommand | None = None,
) -> UserCommandConflictOutcome:
    """Apply the MA1-T09 command conflict and fencing semantics.

    Recorded-command comparison happens first so an already accepted command
    can be replayed safely without being re-applied against a newer wait.
    Every other identity mismatch is rejected before payload execution.
    """

    if recorded_command is not None and recorded_command.command_id == command.command_id:
        if recorded_command.payload_sha256 == command.payload_sha256:
            return UserCommandConflictOutcome(
                command_id=command.command_id,
                disposition="REPLAY",
                reason_code="idempotent_replay",
            )
        return UserCommandConflictOutcome(
            command_id=command.command_id,
            disposition="CONFLICT",
            reason_code="command_payload_conflict",
        )

    if command.execution_id != wait_handle.execution_id:
        return _reject(command, "wrong_execution")
    if command.wait_id != wait_handle.wait_id:
        return _reject(command, "wrong_wait")
    if command.task_id != wait_handle.task_id:
        return _reject(command, "late_task")
    if command.question_id != wait_handle.question_id:
        return _reject(command, "wrong_question")
    if command.command_kind != wait_handle.expected_command_kind:
        return _reject(command, "unexpected_command_kind")
    if command.expected_revision != wait_handle.issued_revision:
        return _stale(command)
    if command.expected_revision != current_revision:
        return _stale(command)
    return UserCommandConflictOutcome(
        command_id=command.command_id,
        disposition="ACCEPT",
        reason_code="accepted",
    )


def _reject(command: UserCommand, reason_code: str) -> UserCommandConflictOutcome:
    return UserCommandConflictOutcome(
        command_id=command.command_id,
        disposition="REJECT",
        reason_code=reason_code,
    )


def _stale(command: UserCommand) -> UserCommandConflictOutcome:
    return UserCommandConflictOutcome(
        command_id=command.command_id,
        disposition="STALE",
        reason_code="stale_revision",
    )


__all__ = [
    "CommandDisposition",
    "UserCommandConflictOutcome",
    "classify_user_command",
]
