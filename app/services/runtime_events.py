import json
from typing import Any, Literal

from pydantic import BaseModel, Field


class InterviewStreamChunkEvent(BaseModel):
    event: Literal["chunk"] = "chunk"
    delta: str

    def to_sse(self) -> str:
        payload = self.model_dump()
        event_name = payload.pop("event")
        return _format_sse(event_name, payload)


class InterviewStreamDoneEvent(BaseModel):
    event: Literal["done"] = "done"
    turn: dict[str, Any]

    def to_sse(self) -> str:
        return _format_sse(self.event, self.turn)


class InterviewStreamErrorEvent(BaseModel):
    event: Literal["error"] = "error"
    detail: str

    def to_sse(self) -> str:
        payload = self.model_dump()
        event_name = payload.pop("event")
        return _format_sse(event_name, payload)


class ReportProgressEvent(BaseModel):
    session_id: str
    status: Literal["processing", "completed", "failed"]
    stage: str
    percent: int = Field(ge=0, le=100)
    message: str
    report_job_id: str | None = None
    current_question_id: str | None = None
    events: list[dict[str, Any]] = Field(default_factory=list)
    rag: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AcceptedInterviewCommand(BaseModel):
    session_id: str
    command_id: str
    status: Literal["pending"] = "pending"
    workflow_engine: Literal["langgraph-v1"] = "langgraph-v1"
    stream_url: str


class InterviewGenerationChunkEvent(BaseModel):
    event: Literal["chunk"] = "chunk"
    generation_id: str
    attempt_number: int
    sequence: int
    delta: str

    def to_sse(self) -> str:
        event_id = (
            f"{self.generation_id}:{self.attempt_number}:{self.sequence}"
        )
        return _format_sse(
            self.event,
            self.model_dump(exclude={"event"}),
            event_id,
        )


class InterviewGenerationResetEvent(BaseModel):
    event: Literal["generation_reset"] = "generation_reset"
    generation_id: str
    attempt_number: int

    def to_sse(self) -> str:
        event_id = f"{self.generation_id}:{self.attempt_number}:0"
        return _format_sse(
            self.event,
            self.model_dump(exclude={"event"}),
            event_id,
        )


def _format_sse(
    event_name: str,
    payload: dict[str, Any],
    event_id: str | None = None,
) -> str:
    prefix = f"id: {event_id}\n" if event_id is not None else ""
    return (
        f"{prefix}event: {event_name}\n"
        f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
    )
