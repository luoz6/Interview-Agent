from app.a2a.cards.common import AgentCard, AgentSkill


EXAMINER_AGENT_CARD = AgentCard(
    agent_id="interview-examiner",
    name="Examiner Agent",
    description="Generates grounded interview questions and follow-ups.",
    skills=[
        AgentSkill(
            name="generate-main-question",
            description="Render one main question from an immutable intent.",
        ),
        AgentSkill(
            name="generate-followup",
            description="Generate one follow-up for the current question.",
        ),
    ],
)
