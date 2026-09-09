from __future__ import annotations

from a2a import types

from app.a2a.cards import (
    EXAMINER_AGENT_CARD,
    KNOWLEDGE_AGENT_CARD,
    REVIEWER_AGENT_CARD,
    REPORT_COACH_AGENT_CARD,
)
from app.a2a.official import to_official_agent_card


OFFICIAL_AGENT_CARDS = {
    "interview-examiner": to_official_agent_card(EXAMINER_AGENT_CARD),
    "knowledge-and-grounding": to_official_agent_card(KNOWLEDGE_AGENT_CARD),
    "interview-reviewer": to_official_agent_card(REVIEWER_AGENT_CARD),
    "report-coach": to_official_agent_card(REPORT_COACH_AGENT_CARD),
}


def official_card_for(agent_id: str) -> types.AgentCard | None:
    return OFFICIAL_AGENT_CARDS.get(agent_id)
