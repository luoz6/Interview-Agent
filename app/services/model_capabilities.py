from __future__ import annotations

from dataclasses import dataclass


class ContextConfigurationError(ValueError):
    """The configured model cannot provide a safe context budget."""


@dataclass(frozen=True)
class ModelRuntimeProfile:
    provider: str
    model: str
    context_window_tokens: int
    protocol_reserve_tokens: int = 512
    structured_output_reserve_tokens: int = 0
    safety_margin_tokens: int = 1024

    def __post_init__(self) -> None:
        if not self.provider.strip():
            raise ContextConfigurationError("provider must not be empty")
        if not self.model.strip():
            raise ContextConfigurationError("model must not be empty")
        if self.context_window_tokens <= 0:
            raise ContextConfigurationError("context window must be positive")
        for name, value in (
            ("protocol reserve", self.protocol_reserve_tokens),
            ("structured output reserve", self.structured_output_reserve_tokens),
            ("safety margin", self.safety_margin_tokens),
        ):
            if value < 0:
                raise ContextConfigurationError(f"{name} must not be negative")


class ModelCapabilityRegistry:
    """Resolve only explicit or repository-tested model context windows."""

    _KNOWN_CONTEXT_WINDOWS = {
        "deepseek-v4-pro": 128_000,
        "deepseek-chat": 128_000,
        "gpt-4o": 128_000,
        "gpt-4o-mini": 128_000,
        "gpt-4-turbo": 128_000,
    }

    def resolve(
        self,
        *,
        model: str,
        provider: str = "openai-compatible",
        configured_context_window_tokens: int | None = None,
        protocol_reserve_tokens: int = 512,
        structured_output_reserve_tokens: int = 0,
        safety_margin_tokens: int = 1024,
        custom_base_url: bool = False,
    ) -> ModelRuntimeProfile:
        normalized_model = model.strip()
        explicit = configured_context_window_tokens
        if explicit is None and custom_base_url:
            raise ContextConfigurationError(
                "custom provider requires an explicit context window"
            )
        window = explicit or self._KNOWN_CONTEXT_WINDOWS.get(normalized_model)
        if window is None:
            raise ContextConfigurationError(
                "unknown model requires an explicit context window"
            )
        return ModelRuntimeProfile(
            provider=provider,
            model=normalized_model,
            context_window_tokens=window,
            protocol_reserve_tokens=protocol_reserve_tokens,
            structured_output_reserve_tokens=structured_output_reserve_tokens,
            safety_margin_tokens=safety_margin_tokens,
        )
