from __future__ import annotations

from contextvars import ContextVar
from typing import Any, Mapping

from app.services.context_budget import RenderedPromptMeasurement
from app.services.context_language import ContextLanguageBucket
from app.services.memory_metrics import publish_provider_usage_metric


_provider_context_metadata: ContextVar[dict[str, Any] | None] = ContextVar(
    "provider_context_metadata",
    default=None,
)


def reset_provider_context_metadata() -> None:
    _provider_context_metadata.set({})


def publish_prompt_measurement(
    measurement: RenderedPromptMeasurement,
    *,
    language_bucket: ContextLanguageBucket | None = None,
) -> None:
    metadata = dict(_provider_context_metadata.get() or {})
    metadata.update(
        {
            "estimated_input_tokens": measurement.estimated_input_tokens,
            "available_input_tokens": measurement.available_input_tokens,
            "budget_utilization_basis_points": (
                measurement.budget_utilization_basis_points
            ),
            "estimator_path": measurement.estimator_path,
            "estimator_fallback_used": measurement.estimator_fallback_used,
        }
    )
    if language_bucket is not None:
        metadata["language_bucket"] = language_bucket
    _provider_context_metadata.set(metadata)


def begin_provider_attempt() -> None:
    metadata = dict(_provider_context_metadata.get() or {})
    metadata["provider_attempt_count"] = int(
        metadata.get("provider_attempt_count", 0)
    ) + 1
    _provider_context_metadata.set(metadata)


def publish_provider_response(response: Any) -> None:
    metadata = dict(_provider_context_metadata.get() or {})
    response_metadata = getattr(response, "response_metadata", None)
    if isinstance(response_metadata, Mapping):
        model = response_metadata.get("model_name") or response_metadata.get("model")
        if isinstance(model, str) and model:
            metadata["provider_model"] = model
    usage = _extract_usage(response)
    if usage is None:
        metadata.setdefault("provider_usage_available", False)
        _provider_context_metadata.set(metadata)
        return
    metadata["provider_usage_available"] = True
    for key, value in usage.items():
        metadata[key] = int(metadata.get(key, 0)) + value
    estimated = metadata.get("estimated_input_tokens")
    actual = metadata.get("provider_input_tokens")
    if isinstance(estimated, int) and isinstance(actual, int):
        metadata.update(
            normalize_estimator_error(
                estimated_input_tokens=estimated,
                provider_input_tokens=actual,
            )
        )
    language_bucket = metadata.get("language_bucket", "unknown")
    if language_bucket not in {"zh_hans", "en", "mixed", "other", "unknown"}:
        language_bucket = "unknown"
    publish_provider_usage_metric(
        language_bucket=language_bucket,
        estimated_input_tokens=(estimated if isinstance(estimated, int) else 0),
        provider_input_tokens=int(metadata.get("provider_input_tokens", 0)),
        provider_output_tokens=int(metadata.get("provider_output_tokens", 0)),
    )
    _provider_context_metadata.set(metadata)


def consume_provider_context_metadata() -> dict[str, Any]:
    metadata = dict(_provider_context_metadata.get() or {})
    _provider_context_metadata.set({})
    return metadata


def _extract_usage(response: Any) -> dict[str, int] | None:
    usage_metadata = getattr(response, "usage_metadata", None)
    if isinstance(usage_metadata, Mapping):
        normalized = _normalize_usage(usage_metadata)
        if normalized:
            return normalized
    response_metadata = getattr(response, "response_metadata", None)
    if isinstance(response_metadata, Mapping):
        for key in ("token_usage", "usage"):
            candidate = response_metadata.get(key)
            if isinstance(candidate, Mapping):
                normalized = _normalize_usage(candidate)
                if normalized:
                    return normalized
    return None


def _normalize_usage(usage: Mapping[str, Any]) -> dict[str, int]:
    aliases = {
        "provider_input_tokens": ("input_tokens", "prompt_tokens"),
        "provider_output_tokens": ("output_tokens", "completion_tokens"),
        "provider_total_tokens": ("total_tokens",),
    }
    result: dict[str, int] = {}
    for target, sources in aliases.items():
        for source in sources:
            value = usage.get(source)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                result[target] = value
                break
    cached_sources = (
        (usage, ("cached_input_tokens", "prompt_cache_hit_tokens")),
        (usage.get("input_token_details"), ("cache_read", "cached_tokens")),
        (usage.get("prompt_tokens_details"), ("cached_tokens",)),
    )
    for container, sources in cached_sources:
        if not isinstance(container, Mapping):
            continue
        for source in sources:
            value = container.get(source)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                result["provider_cached_input_tokens"] = value
                break
        if "provider_cached_input_tokens" in result:
            break
    if (
        "provider_total_tokens" not in result
        and "provider_input_tokens" in result
        and "provider_output_tokens" in result
    ):
        result["provider_total_tokens"] = (
            result["provider_input_tokens"] + result["provider_output_tokens"]
        )
    return result


def normalize_estimator_error(
    *,
    estimated_input_tokens: int,
    provider_input_tokens: int,
) -> dict[str, int | str]:
    if estimated_input_tokens < 0 or provider_input_tokens < 0:
        raise ValueError("token measurements must not be negative")
    delta = estimated_input_tokens - provider_input_tokens
    direction = "exact"
    if delta < 0:
        direction = "under"
    elif delta > 0:
        direction = "over"
    return {
        "estimator_error_direction": direction,
        "estimator_error_basis_points": round(
            abs(delta) * 10_000 / max(1, provider_input_tokens)
        ),
    }
