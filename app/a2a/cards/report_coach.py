from app.a2a.cards.common import AgentCard, AgentSkill


REPORT_COACH_AGENT_CARD = AgentCard(
    agent_id="report-coach",
    name="Report Coach Agent",
    description="Aggregates evaluations into a coaching report.",
    skills=[
        AgentSkill(
            name="generate-report",
            description="Generate the final report from evaluation artifacts.",
        ),
        AgentSkill(
            name="repair-report",
            description="Repair a report that fails quality policy.",
        ),
        AgentSkill(
            name="generate-action-plan",
            description="Generate a traceable action plan.",
        ),
    ],
)
