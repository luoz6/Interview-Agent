from app.a2a.cards.common import AgentCard, AgentSkill


KNOWLEDGE_AGENT_CARD = AgentCard(
    agent_id="knowledge-and-grounding",
    name="Knowledge & Grounding Agent",
    description="Retrieves and grounds role/plan/question evidence.",
    skills=[
        AgentSkill(
            name="generate-interview-plan",
            description="Generate a launchable interview plan.",
        ),
    ],
)
