from app.a2a.cards.common import AgentCard, AgentSkill


KNOWLEDGE_AGENT_CARD = AgentCard(
    agent_id="knowledge-and-grounding",
    name="Knowledge & Grounding Agent",
    description="Retrieves and grounds role/plan/question evidence.",
    skills=[
        AgentSkill(
            name="analyze-role",
            description="Analyze role profile from JD and resume.",
        ),
        AgentSkill(
            name="generate-interview-plan",
            description="Generate a launchable interview plan.",
        ),
        AgentSkill(
            name="retrieve-grounding",
            description="Retrieve and bind evidence for a question.",
        ),
        AgentSkill(
            name="ground-question",
            description="Ground a question against legal evidence scope.",
        ),
    ],
)
