from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class AgentSkill(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1)
    description: str = Field(min_length=1)


class AgentCard(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    agent_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    version: str = Field(default="1.0")
    description: str = Field(min_length=1)
    skills: list[AgentSkill] = Field(min_length=1)
    default_transport: str = Field(default="local")
