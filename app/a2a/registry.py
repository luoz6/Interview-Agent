from __future__ import annotations

from dataclasses import dataclass, field

from app.a2a.cards.common import AgentCard
from app.a2a.server import LocalA2AServer


@dataclass
class AgentRegistry:
    cards: list[AgentCard] = field(default_factory=list)
    server: LocalA2AServer | None = None

    def register_card(self, card: AgentCard) -> None:
        self.cards.append(card)

    def validate(self, server: LocalA2AServer) -> list[str]:
        errors: list[str] = []
        registered = server.registered_skills
        for card in self.cards:
            card_skills = {(card.agent_id, skill.name) for skill in card.skills}
            missing = sorted(card_skills - registered)
            extra = sorted(
                {
                    (agent_id, skill)
                    for agent_id, skill in registered
                    if agent_id == card.agent_id
                }
                - card_skills
            )
            if missing:
                errors.append(f"{card.agent_id} missing server skills: {missing}")
            if extra:
                errors.append(f"{card.agent_id} unexpected server skills: {extra}")
        return errors
