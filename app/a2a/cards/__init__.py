"""A2A-V1 Agent Cards."""

from app.a2a.cards.common import AgentCard, AgentSkill
from app.a2a.cards.examiner import EXAMINER_AGENT_CARD
from app.a2a.cards.knowledge import KNOWLEDGE_AGENT_CARD
from app.a2a.cards.reviewer import REVIEWER_AGENT_CARD
from app.a2a.cards.report_coach import REPORT_COACH_AGENT_CARD

__all__ = [
    "AgentCard",
    "AgentSkill",
    "EXAMINER_AGENT_CARD",
    "KNOWLEDGE_AGENT_CARD",
    "REVIEWER_AGENT_CARD",
    "REPORT_COACH_AGENT_CARD",
]
