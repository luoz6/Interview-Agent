from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from time import monotonic

from app.services.memory_metrics import publish_principal_read_shadow_metric


@dataclass(frozen=True)
class PrincipalMemoryShadowResult:
    provider_context: list[dict[str, str]]
    would_select_count: int
    conflict_count: int
    estimated_tokens: int
    outcome: str


class PrincipalMemoryShadowService:
    def __init__(self, *, retriever):
        self.retriever = retriever

    def observe(
        self, *, provider_context: list[dict[str, str]], current_tags: set[str],
        role_tags: set[str], now,
    ) -> PrincipalMemoryShadowResult:
        started = monotonic()
        original = deepcopy(provider_context)
        try:
            selection = self.retriever.select(
                current_tags=current_tags,
                role_tags=role_tags,
                now=now,
            )
            outcome = "completed"
        except Exception:
            selection = None
            outcome = "failed"
        latency = max(0, round((monotonic() - started) * 1000))
        if provider_context != original:
            provider_context[:] = original
            outcome = "failed"
        source_count = selection.source_count if selection else 0
        selected_count = len(selection.selected) if selection else 0
        estimated = selection.estimated_tokens if selection else 0
        publish_principal_read_shadow_metric(
            outcome=outcome,
            source_count=source_count,
            selected_count=selected_count,
            dropped_count=max(0, source_count - selected_count),
            estimated_input_tokens=estimated,
            latency_ms=latency,
        )
        return PrincipalMemoryShadowResult(
            provider_context=provider_context,
            would_select_count=selected_count,
            conflict_count=selection.conflict_count if selection else 0,
            estimated_tokens=estimated,
            outcome=outcome,
        )
