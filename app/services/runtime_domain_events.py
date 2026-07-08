from typing import Literal

from pydantic import BaseModel, Field

from app.services.report import utc_now_iso


class RoundClosedEvent(BaseModel):
    event_type: Literal["round_closed"] = "round_closed"
    session_id: str
    question_id: str
    answer_state: Literal["answered", "skipped", "unanswered"]
    job_tags: list[str] = Field(default_factory=list)
    emitted_at: str = Field(default_factory=utc_now_iso)
