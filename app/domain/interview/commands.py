from dataclasses import dataclass
from typing import Literal


SessionCommandType = Literal["answer", "finish", "skip"]


@dataclass(frozen=True)
class SessionCommand:
    session_id: str
    command_type: SessionCommandType
    expected_version: int | None = None
    command_id: str | None = None
    answer_text: str | None = None

    @classmethod
    def answer(
        cls,
        session_id: str,
        answer_text: str,
        *,
        expected_version: int | None = None,
        command_id: str | None = None,
    ) -> "SessionCommand":
        return cls(
            session_id=session_id,
            command_type="answer",
            expected_version=expected_version,
            command_id=command_id,
            answer_text=answer_text,
        )


__all__ = ["SessionCommand", "SessionCommandType"]
