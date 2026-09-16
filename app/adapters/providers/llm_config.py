"""Provider-facing configuration for the OpenAI-compatible interview LLM."""

from dataclasses import dataclass
from typing import Literal

from app.runtime.config import load_llm_runtime_settings, load_provider_credentials


RAW_ONLY_PLAN_MODELS = frozenset({"deepseek-v4-pro"})
RAW_ONLY_REPORT_MODELS = frozenset({"deepseek-v4-pro"})
PLAN_SDK_MAX_RETRIES = 1


class MissingLLMConfigError(RuntimeError):
    """LLM configuration is missing, usually OPENAI_API_KEY."""


def resolve_plan_output_mode(
    model: str,
) -> Literal["structured_first", "raw_only"]:
    """Choose the production plan protocol before any Provider request."""

    return "raw_only" if model in RAW_ONLY_PLAN_MODELS else "structured_first"


def resolve_report_output_mode(
    model: str,
) -> Literal["structured_first", "raw_only"]:
    """Choose the report transport supported by the configured model."""

    return "raw_only" if model in RAW_ONLY_REPORT_MODELS else "structured_first"


@dataclass(frozen=True)
class LLMConfig:
    api_key: str
    model: str = "deepseek-v4-pro"
    base_url: str | None = None
    temperature: float = 0.2
    request_timeout_seconds: float = 120.0
    max_retries: int = 1
    context_window_tokens: int | None = None
    protocol_reserve_tokens: int = 512
    structured_output_reserve_tokens: int = 2048
    context_safety_margin_tokens: int = 1024
    tokenizer_family: str | None = None
    plan_output_mode: Literal["structured_first", "raw_only"] = "structured_first"

    def __post_init__(self) -> None:
        if (
            isinstance(self.max_retries, bool)
            or not isinstance(self.max_retries, int)
            or not 0 <= self.max_retries <= PLAN_SDK_MAX_RETRIES
        ):
            raise ValueError(
                "LLM SDK max_retries must be an integer between 0 and at most 1"
            )

    @classmethod
    def from_env(cls, *, memory=None) -> "LLMConfig":
        from app.runtime.config.memory import load_effective_memory_config

        api_key = load_provider_credentials().openai_api_key
        if not api_key:
            raise MissingLLMConfigError("OPENAI_API_KEY is required")

        if memory is None:
            memory = load_effective_memory_config().model
        runtime_settings = load_llm_runtime_settings()
        return cls(
            api_key=api_key,
            model=memory.model,
            base_url=runtime_settings.base_url,
            temperature=runtime_settings.temperature,
            request_timeout_seconds=runtime_settings.request_timeout_seconds,
            max_retries=runtime_settings.max_retries,
            context_window_tokens=memory.context_window_tokens,
            protocol_reserve_tokens=memory.protocol_reserve_tokens,
            structured_output_reserve_tokens=(
                memory.structured_output_reserve_tokens
            ),
            context_safety_margin_tokens=memory.safety_margin_tokens,
            tokenizer_family=memory.tokenizer_family,
            plan_output_mode=resolve_plan_output_mode(memory.model),
        )
