from app.a2a.cards.common import AgentCard, AgentSkill


REVIEWER_AGENT_CARD = AgentCard(
    agent_id="interview-reviewer",
    name="Interview Reviewer Agent",
    description="Produces evidence-bound question and interview evaluations.",
    skills=[
        AgentSkill(
            name="evaluate-answer",
            description="Evaluate a single candidate answer.",
        ),
        AgentSkill(
            name="evaluate-interview",
            description="Evaluate the complete interview session.",
        ),
    ],
)
