from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import re
from typing import Any, Mapping

from app.services.context_budget import RenderedPromptMeasurement
from app.services.context_language import ContextLanguageBucket
from app.services.interview_question_quality import HARD_QUESTION_QUALITY_CODES
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


def publish_plan_quality_repair_lifecycle(
    *,
    initial_hard_finding_codes: tuple[str, ...],
    quality_repair_triggered: bool,
    quality_repair_succeeded: bool | None,
    post_repair_hard_finding_codes: tuple[str, ...] | None,
    quality_repair_prompt_version: str,
    quality_repair_prompt_sha256: str,
) -> None:
    """Publish only frozen, non-content plan-repair lifecycle evidence."""

    allowed_codes = frozenset(HARD_QUESTION_QUALITY_CODES)
    if (
        len(initial_hard_finding_codes) != len(set(initial_hard_finding_codes))
        or any(code not in allowed_codes for code in initial_hard_finding_codes)
    ):
        raise ValueError("initial plan Hard finding codes are not allowlisted")
    if post_repair_hard_finding_codes is not None and (
        len(post_repair_hard_finding_codes)
        != len(set(post_repair_hard_finding_codes))
        or any(code not in allowed_codes for code in post_repair_hard_finding_codes)
    ):
        raise ValueError("post-repair Hard finding codes are not allowlisted")
    if not isinstance(quality_repair_triggered, bool):
        raise ValueError("quality_repair_triggered must be boolean")
    if quality_repair_succeeded is not None and not isinstance(
        quality_repair_succeeded, bool
    ):
        raise ValueError("quality_repair_succeeded must be boolean or null")
    if not quality_repair_triggered and (
        initial_hard_finding_codes
        or quality_repair_succeeded is not None
        or post_repair_hard_finding_codes is not None
    ):
        raise ValueError("non-triggered repair lifecycle cannot claim findings")
    if quality_repair_triggered and not initial_hard_finding_codes:
        raise ValueError("triggered repair lifecycle requires an initial Hard finding")
    if quality_repair_succeeded is True and post_repair_hard_finding_codes != ():
        raise ValueError("successful repair lifecycle requires zero post findings")
    if quality_repair_succeeded is False and post_repair_hard_finding_codes is None:
        raise ValueError("failed repair lifecycle requires known post findings")
    if quality_repair_succeeded is None and post_repair_hard_finding_codes is not None:
        raise ValueError("unknown repair outcome cannot claim post findings")
    if re.fullmatch(
        r"[a-z0-9][a-z0-9.-]{0,127}", quality_repair_prompt_version
    ) is None:
        raise ValueError("quality repair prompt version is invalid")
    if re.fullmatch(r"[0-9a-f]{64}", quality_repair_prompt_sha256) is None:
        raise ValueError("quality repair prompt SHA-256 is invalid")

    metadata = dict(_provider_context_metadata.get() or {})
    metadata.update(
        {
            "initial_hard_finding_codes": list(initial_hard_finding_codes),
            "quality_repair_triggered": quality_repair_triggered,
            "quality_repair_succeeded": quality_repair_succeeded,
            "post_repair_hard_finding_codes": (
                None
                if post_repair_hard_finding_codes is None
                else list(post_repair_hard_finding_codes)
            ),
            "quality_repair_prompt_version": quality_repair_prompt_version,
            "quality_repair_prompt_sha256": quality_repair_prompt_sha256,
        }
    )
    _provider_context_metadata.set(metadata)


def publish_provider_response(response: Any) -> None:
    metadata = dict(_provider_context_metadata.get() or {})
    response_metadata = getattr(response, "response_metadata", None)
    model = None
    if isinstance(response_metadata, Mapping):
        model = response_metadata.get("model_name") or response_metadata.get("model")
    if model is None:
        model = getattr(response, "model", None)
    if isinstance(model, str) and re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}", model
    ):
        observed_models = list(metadata.get("provider_response_models", []))
        observed_models.append(model)
        metadata["provider_response_models"] = observed_models
        metadata["provider_model"] = model
    response_id = _provider_response_id(response, response_metadata)
    if response_id is not None:
        response_hashes = list(metadata.get("provider_response_id_sha256s", []))
        response_hashes.append(hashlib.sha256(response_id.encode("utf-8")).hexdigest())
        metadata["provider_response_id_sha256s"] = response_hashes
    allow_missing_cached_input_tokens = model == "deepseek-v4-pro"
    has_explicit_cached_input_tokens, has_cache_miss_alias = (
        _usage_cache_observation(response)
    )
    usage = extract_provider_usage(
        response,
        allow_partial=_compression_usage_scope.get() is not None,
        allow_missing_cached_input_tokens=allow_missing_cached_input_tokens,
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
    if allow_missing_cached_input_tokens and not has_explicit_cached_input_tokens:
        counter = (
            "provider_usage_cache_derived_count"
            if has_cache_miss_alias
            else "provider_usage_cache_defaulted_count"
        )
        metadata[counter] = int(metadata.get(counter, 0)) + 1
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


def _provider_response_id(
    response: Any,
    response_metadata: Mapping[str, Any] | None,
) -> str | None:
    """Return a bounded response identity for immediate hashing, never storage."""

    candidates: list[Any] = []
    if isinstance(response_metadata, Mapping):
        candidates.extend(
            (response_metadata.get("response_id"), response_metadata.get("id"))
        )
    candidates.append(getattr(response, "id", None))
    for value in candidates:
        if (
            isinstance(value, str)
            and value
            and len(value.encode("utf-8")) <= 512
            and "\x00" not in value
        ):
            return value
    return None


def consume_provider_context_metadata() -> dict[str, Any]:
    metadata = dict(_provider_context_metadata.get() or {})
    _provider_context_metadata.set({})
    return metadata


def extract_provider_usage(
    response: Any,
    *,
    allow_partial: bool = False,
    allow_missing_cached_input_tokens: bool = False,
) -> dict[str, int] | None:
    """Normalize complete Provider usage from supported response metadata shapes."""

    candidates = _usage_candidates(response)
    deepseek_default: dict[str, int] | None = None
    for candidate in candidates:
        normalized = _normalize_usage(candidate)
        if all(key in normalized for key in _REQUIRED_USAGE_KEYS):
            return normalized
        if (
            allow_missing_cached_input_tokens
            and "provider_input_tokens" in normalized
            and "provider_output_tokens" in normalized
        ):
            candidate_default = dict(normalized)
            candidate_default.setdefault("provider_cached_input_tokens", 0)
            candidate_default.setdefault(
                "provider_total_tokens",
                candidate_default["provider_input_tokens"]
                + candidate_default["provider_output_tokens"],
            )
            if deepseek_default is None:
                deepseek_default = candidate_default
        if allow_partial and "provider_input_tokens" in normalized:
            normalized.setdefault("provider_output_tokens", 0)
            normalized.setdefault("provider_cached_input_tokens", 0)
            normalized.setdefault(
                "provider_total_tokens",
                normalized["provider_input_tokens"]
                + normalized["provider_output_tokens"],
            )
            return normalized
    return deepseek_default


def _usage_candidates(response: Any) -> list[Mapping[str, Any]]:
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
    return candidates


def _usage_cache_observation(response: Any) -> tuple[bool, bool]:
    explicit = False
    has_cache_miss_alias = False
    for candidate in _usage_candidates(response):
        if any(
            key in candidate
            for key in ("cached_input_tokens", "prompt_cache_hit_tokens")
        ):
            explicit = True
        if "prompt_cache_miss_tokens" in candidate:
            has_cache_miss_alias = True
        for nested_key in ("input_token_details", "prompt_tokens_details"):
            nested = candidate.get(nested_key)
            if isinstance(nested, Mapping) and any(
                key in nested for key in ("cache_read", "cached_tokens")
            ):
                explicit = True
    return explicit, has_cache_miss_alias


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
    if "provider_cached_input_tokens" not in result:
        cache_miss = usage.get("prompt_cache_miss_tokens")
        input_tokens = result.get("provider_input_tokens")
        if (
            isinstance(cache_miss, int)
            and not isinstance(cache_miss, bool)
            and cache_miss >= 0
            and isinstance(input_tokens, int)
            and input_tokens >= 0
        ):
            result["provider_cached_input_tokens"] = max(
                0, input_tokens - cache_miss
            )
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
