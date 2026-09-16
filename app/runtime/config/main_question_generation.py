from __future__ import annotations

from app.domain.interview.main_question_generation import (
    MAIN_QUESTION_ATTEMPT_TIMEOUT_SECONDS,
    MAIN_QUESTION_MAX_PROVIDER_INVOCATIONS,
    MAIN_QUESTION_TOTAL_TIMEOUT_SECONDS,
    MainQuestionGenerationSettings,
)
from app.runtime.config.environment import environment_value


def load_main_question_generation_settings() -> MainQuestionGenerationSettings:
    return MainQuestionGenerationSettings(
        max_provider_invocations=_positive_int_environment(
            "MAIN_QUESTION_MAX_PROVIDER_INVOCATIONS",
            MAIN_QUESTION_MAX_PROVIDER_INVOCATIONS,
        ),
        attempt_timeout_seconds=_positive_float_environment(
            "MAIN_QUESTION_ATTEMPT_TIMEOUT_SECONDS",
            MAIN_QUESTION_ATTEMPT_TIMEOUT_SECONDS,
        ),
        total_timeout_seconds=_positive_float_environment(
            "MAIN_QUESTION_TOTAL_TIMEOUT_SECONDS",
            MAIN_QUESTION_TOTAL_TIMEOUT_SECONDS,
        ),
    )


def _positive_int_environment(name: str, default: int) -> int:
    raw = environment_value(name, str(default))
    try:
        value = int(str(raw))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a positive integer") from exc
    if value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _positive_float_environment(name: str, default: float) -> float:
    raw = environment_value(name, str(default))
    try:
        value = float(str(raw))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be positive") from exc
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


__all__ = ["load_main_question_generation_settings"]
