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


def _format_sse(event_name: str, payload: dict[str, Any]) -> str:
    return f"event: {event_name}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
