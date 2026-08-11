from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import re
from typing import Any, Mapping

from app.services.context_budget import RenderedPromptMeasurement
from app.services.context_language import ContextLanguageBucket
from app.services.memory_metrics import publish_provider_usage_metric


_provider_context_metadata: ContextVar[dict[str, Any] | None] = ContextVar(
    "provider_context_metadata",
    default=None,
)
_REQUIRED_USAGE_KEYS = (
    "provider_input_tokens",
    "provider_output_tokens",
    "provider_cached_input_tokens",
)
_compression_usage_scope: ContextVar[dict[str, str] | None] = ContextVar(
    "compression_provider_usage_scope",
    default=None,
)


def compression_provider_usage_scope(
    *,
    operation: str,
    workflow: str,
    policy_version: str,
    intent_schema_version: str,
    measurement_path: str = "business",
) -> Any:
    """Bind only allowlisted aggregate metadata around one compressor call."""

    if operation not in {"prep", "followup", "evaluate", "report"}:
        raise ValueError("unsupported compression provider operation")
    if workflow not in {"interview", "review", "prep"}:
        raise ValueError("unsupported compression provider workflow")
    if measurement_path not in {"business", "counterfactual"}:
        raise ValueError("unsupported compression provider measurement_path")
    for name, value in (
        ("policy_version", policy_version),
        ("intent_schema_version", intent_schema_version),
    ):
        if not isinstance(value, str) or re.fullmatch(
            r"[a-z0-9][a-z0-9.-]{0,127}", value
        ) is None:
            raise ValueError(f"unsupported compression provider {name}")
    scope = {
        "operation": operation,
        "workflow": workflow,
        "policy_version": policy_version,
        "intent_schema_version": intent_schema_version,
        "measurement_path": measurement_path,
    }

    return _bind_compression_provider_usage_scope(scope)


@contextmanager
def _bind_compression_provider_usage_scope(scope: dict[str, str]):
    token = _compression_usage_scope.set(scope)
    try:
        yield
    finally:
        _compression_usage_scope.reset(token)


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


def publish_plan_context_selection(
    *,
    candidate_count: int,
    retained_count: int,
) -> None:
    if candidate_count < 0 or retained_count < 0:
        raise ValueError("plan context counts must be non-negative")
    if retained_count > candidate_count:
        raise ValueError("retained plan context cannot exceed candidates")
    metadata = dict(_provider_context_metadata.get() or {})
    metadata["plan_knowledge_candidate_count"] = candidate_count
    metadata["plan_knowledge_retained_count"] = retained_count
    _provider_context_metadata.set(metadata)


def publish_provider_response(response: Any) -> None:
    metadata = dict(_provider_context_metadata.get() or {})
    response_metadata = getattr(response, "response_metadata", None)
    if isinstance(response_metadata, Mapping):
        model = response_metadata.get("model_name") or response_metadata.get("model")
        if isinstance(model, str) and model:
            metadata["provider_model"] = model
    usage = extract_provider_usage(
        response,
        allow_partial=_compression_usage_scope.get() is not None,
    )
    if usage is None:
        metadata["provider_unmetered_attempt_count"] = int(
            metadata.get("provider_unmetered_attempt_count", 0)
        ) + 1
        metadata["provider_usage_available"] = False
        _provider_context_metadata.set(metadata)
        return
    metadata["provider_metered_attempt_count"] = int(
        metadata.get("provider_metered_attempt_count", 0)
    ) + 1
    metadata["provider_usage_available"] = (
        int(metadata.get("provider_unmetered_attempt_count", 0)) == 0
        and int(metadata.get("provider_metered_attempt_count", 0))
        == int(metadata.get("provider_attempt_count", 0))
    )
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
    try:
        publish_provider_usage_metric(
            language_bucket=language_bucket,
            estimated_input_tokens=(
                estimated if isinstance(estimated, int) else 0
            ),
            provider_input_tokens=int(
                metadata.get("provider_input_tokens", 0)
            ),
            provider_output_tokens=int(
                metadata.get("provider_output_tokens", 0)
            ),
            estimator_error_basis_points=int(
                metadata.get("estimator_error_basis_points", 0)
            ),
            **dict(_compression_usage_scope.get() or {}),
        )
    except Exception:
        pass
    _provider_context_metadata.set(metadata)


def consume_provider_context_metadata() -> dict[str, Any]:
    metadata = dict(_provider_context_metadata.get() or {})
    _provider_context_metadata.set({})
    return metadata


def extract_provider_usage(
    response: Any,
    *,
    allow_partial: bool = False,
) -> dict[str, int] | None:
    """Normalize complete Provider usage from supported response metadata shapes."""

    candidates: list[Mapping[str, Any]] = []
    usage_metadata = getattr(response, "usage_metadata", None)
    if isinstance(usage_metadata, Mapping):
        candidates.append(usage_metadata)
    response_metadata = getattr(response, "response_metadata", None)
    if isinstance(response_metadata, Mapping):
        for key in ("token_usage", "usage"):
            candidate = response_metadata.get(key)
            if isinstance(candidate, Mapping):
                candidates.append(candidate)
    for candidate in candidates:
        normalized = _normalize_usage(candidate)
        if all(key in normalized for key in _REQUIRED_USAGE_KEYS):
            return normalized
        if allow_partial and "provider_input_tokens" in normalized:
            normalized.setdefault("provider_output_tokens", 0)
            normalized.setdefault("provider_cached_input_tokens", 0)
            normalized.setdefault(
                "provider_total_tokens",
                normalized["provider_input_tokens"]
                + normalized["provider_output_tokens"],
            )
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
            min(
                100_000,
                abs(delta) * 10_000 / max(1, provider_input_tokens),
            )
        ),
    }
