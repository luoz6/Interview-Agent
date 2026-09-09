from app.a2a.cards.common import AgentCard, AgentSkill


EXAMINER_AGENT_CARD = AgentCard(
    agent_id="interview-examiner",
    name="Examiner Agent",
    description="Generates grounded, policy-compliant interview follow-ups.",
    skills=[
        AgentSkill(
            name="generate-followup",
            description="Generate one follow-up for the current question.",
        ),
        AgentSkill(
            name="probe-answer-gap",
            description="Probe a selected open gap in the candidate answer.",
        ),
    ],
)
