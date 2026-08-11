from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from time import monotonic
import unicodedata

from app.services.memory_metrics import publish_principal_read_shadow_metric


@dataclass(frozen=True)
class PrincipalMemoryShadowResult:
    provider_context: list[dict[str, str]]
    would_select_count: int
    conflict_count: int
    estimated_tokens: int
    outcome: str


class PrincipalMemoryShadowObserver:
    def __init__(self, *, retriever, mode: str):
        self.retriever = retriever
        self.is_enabled = mode == "read_shadow"

    def observe(
        self, *, provider_context: list[dict[str, str]], current_tags: set[str],
        role_tags: set[str], now, session_id: str | None = None,
    ) -> PrincipalMemoryShadowResult:
        if not self.is_enabled:
            return PrincipalMemoryShadowResult(
                provider_context=provider_context,
                would_select_count=0,
                conflict_count=0,
                estimated_tokens=0,
                outcome="disabled",
            )
        started = monotonic()
        original = canonical_provider_context(provider_context)
        original_digest = sha256(original.encode("utf-8")).hexdigest()
        try:
            selection_args = dict(
                current_tags=current_tags,
                role_tags=role_tags,
                now=now,
            )
            if session_id is not None:
                selection_args["session_id"] = session_id
            selection = self.retriever.select(**selection_args)
            authority_check = getattr(self.retriever, "is_currently_authorized", None)
            currently_authorized = (
                authority_check()
                if authority_check is not None and session_id is None
                else authority_check(session_id=session_id)
                if authority_check is not None
                else True
            )
            if not currently_authorized:
                selection = None
                outcome = "failed"
            else:
                outcome = "completed"
        except Exception:
            selection = None
            outcome = "failed"
        latency = max(0, round((monotonic() - started) * 1000))
        current_digest = canonical_provider_context_digest(provider_context)
        if current_digest != original_digest:
            provider_context[:] = json.loads(original)
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


def canonical_provider_context(value: list[dict[str, str]]) -> str:
    normalized = [
        {
            str(key): unicodedata.normalize("NFC", str(item))
            for key, item in sorted(message.items())
        }
        for message in value
    ]
    return json.dumps(
        normalized, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    )


def canonical_provider_context_digest(value: list[dict[str, str]]) -> str:
    return sha256(canonical_provider_context(value).encode("utf-8")).hexdigest()


PrincipalMemoryShadowService = PrincipalMemoryShadowObserver
