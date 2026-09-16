from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class PrincipalMemoryCandidate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    fact_type: str
    fact: dict[str, str]
    confidence: float = Field(ge=0, le=1)
    exact_excerpt: str = Field(min_length=1, max_length=1000)
    source_message_id: str = Field(min_length=1, max_length=128)
    source_question_id: str | None = Field(default=None, max_length=128)
    direct_user_statement: bool = True


class NullPrincipalMemoryExtractor:
    def extract(self, *, messages: list[dict], max_proposals: int):
        return []


class StructuredPrincipalMemoryExtractor:
    def __init__(self, provider):
        self.provider = provider

    def extract(self, *, messages: list[dict], max_proposals: int):
        if max_proposals < 1:
            raise ValueError("max_proposals must be positive")
        payload = self.provider(messages=messages, max_proposals=max_proposals)
        candidates = [PrincipalMemoryCandidate.model_validate(item) for item in payload]
        return candidates[:max_proposals]
