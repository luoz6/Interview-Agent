from __future__ import annotations

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator

from app.domain.interview.scheduling.commands import (
    UserCommand,
    UserCommandKind,
    normalize_user_command_kind,
)


class WaitHandle(BaseModel):
    """Durable fencing record emitted when an execution enters WAIT_USER."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    wait_id: str = Field(min_length=1)
    execution_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    question_id: str = Field(min_length=1)
    issued_revision: int = Field(ge=0)
    expected_command_kind: UserCommandKind = Field(
        default="ANSWER",
        validation_alias=AliasChoices(
            "expected_command_kind", "command_kind", "kind"
        ),
    )

    @field_validator("expected_command_kind", mode="before")
    @classmethod
    def normalize_expected_command_kind(cls, value: object) -> str:
        return normalize_user_command_kind(value)

    def matches_command(self, command: UserCommand) -> bool:
        """Return whether command identity and kind match this wait fence."""

        return (
            command.execution_id == self.execution_id
            and command.wait_id == self.wait_id
            and command.task_id == self.task_id
            and command.question_id == self.question_id
            and command.expected_revision == self.issued_revision
            and command.command_kind == self.expected_command_kind
        )


__all__ = ["WaitHandle"]
