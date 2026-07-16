from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator

from app.services.report import utc_now_iso


class RuntimeEventEnvelope(BaseModel):
    schema_version: Literal["runtime-event-v1"] = "runtime-event-v1"
    event_id: str = Field(default_factory=lambda: f"event-{uuid4().hex}")
    session_id: str
    correlation_id: str | None = None
    causation_id: str | None = None
    state_version: int | None = Field(default=None, ge=1)
    emitted_at: str = Field(default_factory=utc_now_iso)

    @model_validator(mode="after")
    def default_correlation_to_session(self):
        if not self.correlation_id:
            self.correlation_id = self.session_id
        return self


class RoundClosedEvent(RuntimeEventEnvelope):
    event_type: Literal["round_closed"] = "round_closed"
    question_id: str
    answer_state: Literal["answered", "skipped", "unanswered"]
    job_tags: list[str] = Field(default_factory=list)
