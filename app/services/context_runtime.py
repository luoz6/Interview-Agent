from __future__ import annotations

from dataclasses import dataclass
from threading import Lock

from app.services.context_budget import ContextBudgetResolver
from app.services.model_capabilities import (
    ModelCapabilityRegistry,
    ModelRuntimeProfile,
)
from app.services.token_estimation import (
    CompositeTokenEstimator,
    TokenEstimatorResolution,
)


@dataclass(frozen=True)
class ContextRuntimeConfig:
    provider: str = "openai-compatible"
    model: str = "deepseek-v4-pro"
    base_url: str | None = None
    context_window_tokens: int | None = None
    protocol_reserve_tokens: int = 512
    structured_output_reserve_tokens: int = 2048
    safety_margin_tokens: int = 1024
    tokenizer_family: str | None = None

    @classmethod
    def from_env(cls) -> "ContextRuntimeConfig":
        from app.services.memory_config import load_effective_memory_config

        memory = load_effective_memory_config().model
        return cls(
            provider=memory.provider,
            model=memory.model,
            base_url="custom" if memory.custom_base_url else None,
            context_window_tokens=memory.context_window_tokens,
            protocol_reserve_tokens=memory.protocol_reserve_tokens,
            structured_output_reserve_tokens=(
                memory.structured_output_reserve_tokens
            ),
            safety_margin_tokens=memory.safety_margin_tokens,
            tokenizer_family=memory.tokenizer_family,
        )


@dataclass(frozen=True)
class ContextRuntime:
    model_profile: ModelRuntimeProfile
    estimator_resolution: TokenEstimatorResolution
    budget_resolver: ContextBudgetResolver


@dataclass(frozen=True)
class BudgetShadowObservation:
    source_message_count: int
    hypothetical_selected_count: int
    hypothetical_dropped_count: int
    rendered_prompt_estimate: int
    mandatory_current_preserved: bool
    provider_input_unchanged: bool = True


def build_budget_shadow_observation(
    *,
    source_message_count: int,
    hypothetical_selected_count: int,
    rendered_prompt_estimate: int,
    mandatory_current_preserved: bool,
) -> BudgetShadowObservation:
    if min(
        source_message_count,
        hypothetical_selected_count,
        rendered_prompt_estimate,
    ) < 0:
        raise ValueError("budget shadow counts must be non-negative")
    if hypothetical_selected_count > source_message_count:
        raise ValueError("budget shadow selection cannot exceed source count")
    return BudgetShadowObservation(
        source_message_count=source_message_count,
        hypothetical_selected_count=hypothetical_selected_count,
        hypothetical_dropped_count=(
            source_message_count - hypothetical_selected_count
        ),
        rendered_prompt_estimate=rendered_prompt_estimate,
        mandatory_current_preserved=mandatory_current_preserved,
    )


def build_context_runtime(
    config: ContextRuntimeConfig | None = None,
) -> ContextRuntime:
    config = config or ContextRuntimeConfig.from_env()
    profile = ModelCapabilityRegistry().resolve(
        provider=config.provider,
        model=config.model,
        configured_context_window_tokens=config.context_window_tokens,
        protocol_reserve_tokens=config.protocol_reserve_tokens,
        structured_output_reserve_tokens=config.structured_output_reserve_tokens,
        safety_margin_tokens=config.safety_margin_tokens,
        custom_base_url=bool(config.base_url),
    )
    estimator = CompositeTokenEstimator(
        configured_family=config.tokenizer_family,
    ).resolve(model=profile.model)
    return ContextRuntime(
        model_profile=profile,
        estimator_resolution=estimator,
        budget_resolver=ContextBudgetResolver(),
    )


_context_runtime: ContextRuntime | None = None
_context_runtime_lock = Lock()


def get_context_runtime() -> ContextRuntime:
    global _context_runtime
    if _context_runtime is not None:
        return _context_runtime
    with _context_runtime_lock:
        if _context_runtime is None:
            _context_runtime = build_context_runtime()
    return _context_runtime


def reset_context_runtime_for_tests() -> None:
    global _context_runtime
    with _context_runtime_lock:
        _context_runtime = None
