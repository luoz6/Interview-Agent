from __future__ import annotations

from dataclasses import dataclass, field
from collections.abc import Iterable

from app.a2a.cards.common import AgentCard
from app.a2a.server import LocalA2AServer
from app.domain.interview.scheduling.capabilities import CapabilityDescriptor
from app.ports.agent_capability import AgentCapabilityPort


# Capability identity belongs to the typed scheduling contract, not to the
# A2A card.  Cards remain the transport-facing source of names/descriptions;
# this small adapter table supplies the neutral request/output identities that
# a scheduler must validate before dispatching.  Unknown skills still receive
# a deterministic descriptor so custom cards can be registered without
# weakening the port contract.
_CAPABILITY_METADATA: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "generate-interview-plan": (
        "generate-interview-plan-request",
        "interview-plan-artifact",
        (),
    ),
    "generate-main-question": (
        "generate-main-question-request",
        "main-question-artifact",
        (),
    ),
    "generate-followup": (
        "generate-followup-request",
        "followup-artifact",
        (),
    ),
    "evaluate-answer": (
        "evaluate-answer-request",
        "evaluation-artifact",
        (),
    ),
    "evaluate-interview": (
        "evaluate-interview-request",
        "evaluation-artifact-set",
        (),
    ),
    "generate-report": (
        "generate-report-request",
        "report-artifact",
        ("interview-plan-artifact", "evaluation-artifact"),
    ),
}


def _descriptor_for_skill(
    card: AgentCard,
    *,
    skill: str,
    description: str,
) -> CapabilityDescriptor:
    request_contract_id, output_artifact_type, required_inputs = (
        _CAPABILITY_METADATA.get(
            skill,
            (f"{skill}-request", f"{skill}-artifact", ()),
        )
    )
    output_version = "evaluation-artifact-v2" if skill == "evaluate-answer" else "1.0"
    return CapabilityDescriptor(
        agent_id=card.agent_id,
        skill=skill,
        description=description,
        request_contract_id=request_contract_id,
        request_contract_version="v1",
        output_artifact_type=output_artifact_type,
        output_artifact_version=output_version,
        required_input_artifact_types=required_inputs,
        capability_version="v1",
        supports_streaming=skill in {"generate-main-question", "generate-followup"},
    )


@dataclass
class AgentRegistry(AgentCapabilityPort):
    cards: list[AgentCard] = field(default_factory=list)
    server: LocalA2AServer | None = None
    capabilities: list[CapabilityDescriptor] = field(default_factory=list)

    def __post_init__(self) -> None:
        # Preserve the old ``AgentRegistry(cards=[...])`` construction style
        # while ensuring cards and typed capabilities cannot drift apart.
        existing = tuple(self.capabilities)
        self.capabilities = list(existing)
        for card in tuple(self.cards):
            self._register_card_capabilities(card)

    def register_card(self, card: AgentCard) -> None:
        self.cards.append(card)
        self._register_card_capabilities(card)

    def register_capability(self, capability: CapabilityDescriptor) -> None:
        """Register one explicit typed capability.

        Explicit descriptors are useful for versioned/custom skills and are
        kept in this same registry; no parallel capability catalogue is
        introduced.
        """

        key = capability.capability_key
        for index, current in enumerate(self.capabilities):
            if current.capability_key == key:
                self.capabilities[index] = capability
                return
        self.capabilities.append(capability)

    def list_capabilities(self) -> tuple[CapabilityDescriptor, ...]:
        """Return the typed capabilities exposed by the registered cards."""

        return tuple(self.capabilities)

    def resolve(
        self,
        *,
        agent_id: str,
        skill: str,
        capability_version: str | None = None,
    ) -> CapabilityDescriptor | None:
        matches = tuple(
            capability
            for capability in self.capabilities
            if capability.agent_id == agent_id
            and capability.skill == skill
            and (
                capability_version is None
                or capability.capability_version == capability_version
            )
        )
        # An unversioned lookup is only safe when the identity is unique.
        return matches[0] if len(matches) == 1 else None

    def validate_compatibility(
        self,
        *,
        agent_id: str,
        skill: str,
        request_contract_id: str,
        request_contract_version: str,
        input_artifact_types: Iterable[str] = (),
        capability_version: str | None = None,
    ) -> bool:
        capability = self.resolve(
            agent_id=agent_id,
            skill=skill,
            capability_version=capability_version,
        )
        return capability is not None and capability.is_compatible(
            agent_id=agent_id,
            skill=skill,
            request_contract_id=request_contract_id,
            request_contract_version=request_contract_version,
            input_artifact_types=input_artifact_types,
        )

    def _register_card_capabilities(self, card: AgentCard) -> None:
        for skill in card.skills:
            descriptor = _descriptor_for_skill(
                card,
                skill=skill.name,
                description=skill.description,
            )
            # An explicit typed descriptor is authoritative.  This lets a
            # deployment override adapter defaults without introducing a
            # second registry or silently replacing its contract metadata.
            if not any(
                current.capability_key == descriptor.capability_key
                for current in self.capabilities
            ):
                self.register_capability(descriptor)

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
