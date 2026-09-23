from __future__ import annotations

from collections.abc import Iterable

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CapabilityDescriptor(BaseModel):
    """Typed declaration of an executable Agent capability."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    agent_id: str = Field(min_length=1)
    skill: str = Field(min_length=1)
    description: str = Field(min_length=1)
    request_contract_id: str = Field(min_length=1)
    request_contract_version: str = Field(min_length=1)
    output_artifact_type: str = Field(min_length=1)
    output_artifact_version: str = Field(min_length=1)
    required_input_artifact_types: tuple[str, ...] = ()
    capability_version: str = Field(min_length=1)
    supports_streaming: bool = False

    @field_validator("required_input_artifact_types")
    @classmethod
    def validate_input_types(cls, value: Iterable[str]) -> tuple[str, ...]:
        normalized = tuple(str(item).strip() for item in value)
        if any(not item for item in normalized):
            raise ValueError("required input artifact types must be non-empty")
        if len(normalized) != len(set(normalized)):
            raise ValueError("required input artifact types must be unique")
        return normalized

    @property
    def capability_key(self) -> str:
        return f"{self.agent_id}:{self.skill}:{self.capability_version}"

    def is_compatible(
        self,
        *,
        agent_id: str,
        skill: str,
        request_contract_id: str,
        request_contract_version: str,
        input_artifact_types: Iterable[str] = (),
    ) -> bool:
        """Require typed contract identity, not only agent and skill names."""

        available = set(input_artifact_types)
        return (
            self.agent_id == agent_id
            and self.skill == skill
            and self.request_contract_id == request_contract_id
            and self.request_contract_version == request_contract_version
            and set(self.required_input_artifact_types).issubset(available)
        )

    def is_output_compatible(
        self,
        *,
        artifact_type: str,
        artifact_version: str,
    ) -> bool:
        """Return whether an actual output identity matches this capability."""

        return (
            artifact_type == self.output_artifact_type
            and artifact_version == self.output_artifact_version
        )


__all__ = ["CapabilityDescriptor"]
