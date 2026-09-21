"""Bounded context selection policy for Agent-private memory."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domain.context.selection import truncate_text_to_tokens
from app.domain.context.token_estimation import TokenEstimator
from app.domain.memory.agent import AgentMemoryRecord


class AgentMemoryContextPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    retrieval_limit: int = Field(default=8, ge=1, le=50)
    max_ttl_seconds: int = Field(default=86_400, ge=1, le=2_592_000)
    summary_mode: Literal["summary_only"] = "summary_only"
    max_context_tokens: int = Field(default=512, ge=1, le=8_000)


DEFAULT_AGENT_MEMORY_CONTEXT_POLICY = AgentMemoryContextPolicy()


class AgentMemoryContextItem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    memory_id: str
    summary: str
    truncated: bool = False


class AgentMemoryContextSelection(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    items: tuple[AgentMemoryContextItem, ...] = ()
    rendered_context: str = ""
    estimated_tokens: int = Field(ge=0)
    expired_count: int = Field(ge=0)
    retrieval_excluded_count: int = Field(ge=0)
    budget_excluded_count: int = Field(ge=0)


def select_agent_memory_context(
    records: tuple[AgentMemoryRecord, ...],
    *,
    policy: AgentMemoryContextPolicy,
    estimator: TokenEstimator,
    model: str,
    now: datetime,
) -> AgentMemoryContextSelection:
    """Select summary-only memory context under expiry, count, and token caps."""

    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")

    active = tuple(record for record in records if record.expires_at > now)
    expired_count = len(records) - len(active)
    ordered = tuple(sorted(active, key=lambda item: item.created_at, reverse=True))
    candidates = ordered[: policy.retrieval_limit]
    retrieval_excluded_count = len(ordered) - len(candidates)

    items: list[AgentMemoryContextItem] = []
    rendered: list[str] = []
    budget_excluded_count = 0
    used_tokens = 0
    for record in candidates:
        prefix = "\n".join(rendered)
        prefix_tokens = estimator.estimate_text(prefix, model=model) if prefix else 0
        separator = "\n" if prefix else ""
        separator_tokens = (
            estimator.estimate_text(f"{prefix}{separator}", model=model)
            - prefix_tokens
            if separator
            else 0
        )
        remaining = policy.max_context_tokens - prefix_tokens - separator_tokens
        bounded, truncated = truncate_text_to_tokens(
            record.summary,
            token_budget=remaining,
            estimator=estimator,
            model=model,
        )
        if not bounded:
            budget_excluded_count += 1
            continue
        candidate_context = f"{prefix}{separator}{bounded}"
        candidate_tokens = estimator.estimate_text(candidate_context, model=model)
        if candidate_tokens > policy.max_context_tokens:
            budget_excluded_count += 1
            continue
        items.append(
            AgentMemoryContextItem(
                memory_id=record.memory_id,
                summary=bounded,
                truncated=truncated,
            )
        )
        rendered.append(bounded)
        used_tokens = candidate_tokens

    return AgentMemoryContextSelection(
        items=tuple(items),
        rendered_context="\n".join(rendered),
        estimated_tokens=used_tokens,
        expired_count=expired_count,
        retrieval_excluded_count=retrieval_excluded_count,
        budget_excluded_count=budget_excluded_count,
    )


__all__ = [
    "AgentMemoryContextItem",
    "AgentMemoryContextPolicy",
    "AgentMemoryContextSelection",
    "DEFAULT_AGENT_MEMORY_CONTEXT_POLICY",
    "select_agent_memory_context",
]
