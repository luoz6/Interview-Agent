from __future__ import annotations

from a2a import types

from app.a2a.cards.common import AgentCard


def to_official_agent_card(card: AgentCard) -> types.AgentCard:
    """Convert the internal stable AgentCard to an official A2A 1.0 card."""

    official = types.AgentCard(
        name=card.name,
        description=card.description,
        version=card.version,
        capabilities=types.AgentCapabilities(streaming=False),
    )
    official.default_input_modes.extend(["text/plain", "application/json"])
    official.default_output_modes.extend(["application/json"])
    for skill in card.skills:
        official.skills.append(
            types.AgentSkill(
                id=skill.name,
                name=skill.name,
                description=skill.description,
            )
        )
    return official
