from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator


UserCommandKind = Literal["ANSWER", "RETRY", "CANCEL", "SKIP", "COMPLETE"]


def normalize_user_command_kind(value: object) -> str:
    normalized = str(value or "").strip().upper()
    aliases = {
        "USER_ANSWER": "ANSWER",
        "CANCEL_TASK": "CANCEL",
        "SKIP_TASK": "SKIP",
    }
    return aliases.get(normalized, normalized)


class UserCommand(BaseModel):
    """Neutral, fenced input used to resume a waiting execution."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    command_id: str = Field(min_length=1)
    execution_id: str = Field(min_length=1)
    wait_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    question_id: str | None = Field(default=None, min_length=1)
    expected_revision: int = Field(ge=0)
    payload: dict[str, Any] = Field(default_factory=dict)
    command_kind: UserCommandKind = Field(
        default="ANSWER",
        validation_alias=AliasChoices(
            "command_kind", "kind", "command_type"
        ),
    )

    @field_validator("command_kind", mode="before")
    @classmethod
    def normalize_command_kind(cls, value: object) -> str:
        return normalize_user_command_kind(value)

    @property
    def kind(self) -> UserCommandKind:
        """Short spelling used by command dispatchers."""

        return self.command_kind

    @property
    def payload_sha256(self) -> str:
        canonical = json.dumps(
            self.payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            default=str,
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()


__all__ = [
    "UserCommand",
    "UserCommandKind",
    "normalize_user_command_kind",
]
