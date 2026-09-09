from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.a2a.cards import (
    EXAMINER_AGENT_CARD,
    KNOWLEDGE_AGENT_CARD,
    REPORT_COACH_AGENT_CARD,
    REVIEWER_AGENT_CARD,
)


router = APIRouter(prefix="/a2a")

_AGENT_CARDS = {
    card.agent_id: card
    for card in (
        EXAMINER_AGENT_CARD,
        KNOWLEDGE_AGENT_CARD,
        REVIEWER_AGENT_CARD,
        REPORT_COACH_AGENT_CARD,
    )
}


class A2ATaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skill: str = Field(min_length=1)
    input: dict[str, Any] = Field(default_factory=dict)


@router.get("/agents")
def list_a2a_agents():
    return {
        "items": [card.model_dump(mode="json") for card in _AGENT_CARDS.values()]
    }


@router.get("/agents/{agent_id}/card")
def get_a2a_agent_card(agent_id: str):
    card = _AGENT_CARDS.get(agent_id)
    if card is None:
        raise HTTPException(status_code=404, detail="agent card not found")
    return card.model_dump(mode="json")


@router.post("/agents/{agent_id}/tasks")
def submit_a2a_agent_task(agent_id: str, payload: A2ATaskRequest):
    if agent_id not in _AGENT_CARDS:
        raise HTTPException(status_code=404, detail="agent card not found")
    from app.a2a.runtime import build_local_a2a_runtime

    runtime = build_local_a2a_runtime()
    artifact = runtime.invoker.invoke(
        agent_id=agent_id,
        skill=payload.skill,
        request=payload.input,
    )
    return artifact.model_dump(mode="json")
