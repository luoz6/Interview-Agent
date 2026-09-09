from app.a2a.cards.common import AgentCard
from app.a2a.cards.examiner import EXAMINER_AGENT_CARD
from app.a2a.cards.knowledge import KNOWLEDGE_AGENT_CARD
from app.a2a.cards.reviewer import REVIEWER_AGENT_CARD
from app.a2a.cards.report_coach import REPORT_COACH_AGENT_CARD


A2A_PLATFORM_AGENT_CARD = AgentCard(
    agent_id="interview-agent-platform",
    name="Interview Agent Platform",
    description="Local A2A-capable interview agent platform.",
    skills=[
        *EXAMINER_AGENT_CARD.skills,
        *KNOWLEDGE_AGENT_CARD.skills,
        *REVIEWER_AGENT_CARD.skills,
        *REPORT_COACH_AGENT_CARD.skills,
    ],
)
