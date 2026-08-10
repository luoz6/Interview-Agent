from dataclasses import dataclass
import math
import re
from typing import Any


AGENT_TRACE_BLOCKED_KEYS = {
    "answer",
    "api_key",
    "authorization",
    "candidate_answer",
    "content",
    "credential",
    "dsn",
    "embedding",
    "excerpt",
    "job_description",
    "password",
    "prompt",
    "provider_error",
    "provider_response",
    "principal_id",
    "fact_id",
    "failure_state_record",
    "normalized_fact",
    "owner_key_sha256",
    "privacy_scope_sha256",
    "probe_owner_sha256",
    "probe_token",
    "state_key_sha256",
    "source_manifest_sha256",
    "source_excerpt_sha256",
    "consent_record",
    "raw_content",
    "raw_response",
    "resume",
    "resume_text",
    "secret",
    "summary",
    "token",
    "user_answer",
    "validation_payload",
}

KNOWLEDGE_TRACE_BLOCKED_KEY_PARTS = (
    "api_key",
    "authorization",
    "content",
    "dsn",
    "embedding",
    "password",
    "provider_response",
    "raw_response",
    "resume",
    "secret",
    "token",
)

AGENT_SAFE_METADATA_BLOCKED_KEY_PARTS = tuple(
    sorted(AGENT_TRACE_BLOCKED_KEYS)
)
_SAFE_METADATA_KEY = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
_SAFE_METADATA_STRING = re.compile(r"^[A-Za-z0-9_.:@+\-/]{1,128}$")
_SAFE_METADATA_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SAFE_METADATA_SHA256_KEYS = frozenset(
    {"configuration_sha256", "plan_sha256"}
)
_WINDOWS_ABSOLUTE_PATH = re.compile(r"^[A-Za-z]:[\\/]")
_SENSITIVE_METADATA_VALUE = re.compile(
    r"(?:^[Bb]earer(?:[ .:]|$)|^sk-[A-Za-z0-9_-]{8,}|://|"
    r"^[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}$)"
)
_MISSING = object()
AGENT_SAFE_METADATA_MAX_DEPTH = 3
AGENT_SAFE_METADATA_MAX_ITEMS = 50
# String metadata is deliberately schema-like rather than free-form. Counts,
# booleans and finite numbers remain generally safe, while strings are accepted
# only for the machine fields used by the Agent Runtime contract. This prevents
# a benign key such as ``value`` or ``debug`` from carrying a DSN, token,
# candidate answer or provider payload into both recorders.
AGENT_SAFE_METADATA_STRING_KEYS = frozenset(
    {
        "report_path",
        "retrieval_path",
        "knowledge_status",
        "question_ids",
        "reused_question_ids",
        "rerun_question_ids",
        "failed_question_ids",
        "context_policy_version",
        "configuration_sha256",
        "plan_sha256",
        "generator_version",
        "budget_version",
        "generation_enforcement_action",
        "artifact_type",
        "estimator_path",
        "estimator_error_direction",
        "provider_model",
        "failure_code",
        "report_path",
        "store_outcome",
    }
)
AGENT_CONTEXT_NUMERIC_METADATA_KEYS = frozenset(
    {
        "estimated_input_tokens",
        "provider_input_tokens",
        "provider_output_tokens",
        "provider_cached_input_tokens",
        "provider_total_tokens",
        "available_input_tokens",
        "context_window_tokens",
        "budget_utilization_basis_points",
        "source_message_count",
        "selected_message_count",
        "selected_count",
        "would_select_count",
        "conflict_count",
        "dropped_message_count",
        "truncated_message_count",
        "source_evidence_count",
        "selected_evidence_count",
        "dropped_evidence_count",
        "truncated_evidence_count",
        "provider_attempt_count",
        "source_segment_count",
        "target_output_tokens",
        "estimator_error_basis_points",
        "first_item_latency_ms",
    }
)
AGENT_CONTEXT_BOOLEAN_METADATA_KEYS = frozenset(
    {
        "estimator_fallback_used",
        "deterministic_shrink_used",
        "provider_usage_available",
        "provider_metered_attempt_count",
        "provider_unmetered_attempt_count",
        "plan_knowledge_candidate_count",
        "plan_knowledge_retained_count",
    }
)


@dataclass(frozen=True)
class SanitizedAgentMetadata:
    value: dict[str, Any]
    rejected_count: int
    rejection_categories: tuple[str, ...]


def sanitize_agent_safe_metadata(value: Any) -> SanitizedAgentMetadata:
    """Return bounded machine metadata without stringifying rejected values."""

    categories: set[str] = set()
    rejected_count = 0

    def reject(category: str):
        nonlocal rejected_count
        rejected_count += 1
        categories.add(category)
        return _MISSING

    def sanitize(item: Any, *, depth: int, field_name: str | None = None):
        if depth > AGENT_SAFE_METADATA_MAX_DEPTH:
            return reject("max_depth")
        if item is None or isinstance(item, bool) or isinstance(item, int):
            return item
        if isinstance(item, float):
            return item if math.isfinite(item) else reject("non_finite_number")
        if isinstance(item, str):
            if (
                field_name in _SAFE_METADATA_SHA256_KEYS
                and _SAFE_METADATA_SHA256.fullmatch(item) is None
            ):
                return reject("invalid_sha256")
            if (
                _WINDOWS_ABSOLUTE_PATH.match(item)
                or item.startswith("/")
            ):
                return reject("absolute_path")
            if field_name not in AGENT_SAFE_METADATA_STRING_KEYS:
                return reject("undeclared_string_field")
            if _SENSITIVE_METADATA_VALUE.search(item):
                return reject("sensitive_string")
            if _SAFE_METADATA_STRING.fullmatch(item) is None:
                return reject("unsafe_string")
            return item
        if isinstance(item, dict):
            if len(item) > AGENT_SAFE_METADATA_MAX_ITEMS:
                return reject("max_items")
            result: dict[str, Any] = {}
            for raw_key, raw_value in item.items():
                if not isinstance(raw_key, str):
                    reject("invalid_key")
                    continue
                context_numeric = raw_key in AGENT_CONTEXT_NUMERIC_METADATA_KEYS
                context_boolean = raw_key in AGENT_CONTEXT_BOOLEAN_METADATA_KEYS
                if (
                    _SAFE_METADATA_KEY.fullmatch(raw_key) is None
                    or is_blocked_trace_key(
                        raw_key,
                        blocked_keys=AGENT_TRACE_BLOCKED_KEYS,
                        blocked_key_parts=(
                            AGENT_SAFE_METADATA_BLOCKED_KEY_PARTS
                        ),
                    )
                    and not context_numeric
                ):
                    reject("blocked_key")
                    continue
                if context_numeric and (
                    isinstance(raw_value, bool)
                    or not isinstance(raw_value, (int, float))
                    or (isinstance(raw_value, float) and not math.isfinite(raw_value))
                    or raw_value < 0
                ):
                    reject("invalid_context_numeric")
                    continue
                if context_boolean and not isinstance(raw_value, bool):
                    reject("invalid_context_boolean")
                    continue
                sanitized = sanitize(
                    raw_value,
                    depth=depth + 1,
                    field_name=raw_key,
                )
                if sanitized is _MISSING:
                    continue
                if (
                    isinstance(raw_value, (dict, list, tuple))
                    and raw_value
                    and not sanitized
                ):
                    continue
                result[raw_key] = sanitized
            return result
        if isinstance(item, (list, tuple)):
            if len(item) > AGENT_SAFE_METADATA_MAX_ITEMS:
                return reject("max_items")
            result = []
            for child in item:
                sanitized = sanitize(
                    child,
                    depth=depth + 1,
                    field_name=field_name,
                )
                if sanitized is not _MISSING:
                    result.append(sanitized)
            return result
        return reject("unsupported_type")

    if not isinstance(value, dict):
        reject("root_not_mapping")
        sanitized_value: dict[str, Any] = {}
    else:
        resolved = sanitize(value, depth=0)
        sanitized_value = resolved if isinstance(resolved, dict) else {}
    return SanitizedAgentMetadata(
        value=sanitized_value,
        rejected_count=rejected_count,
        rejection_categories=tuple(sorted(categories)),
    )


def sanitize_trace_payload(
    value: Any,
    *,
    blocked_keys=frozenset(),
    blocked_key_parts=(),
):
    if isinstance(value, dict):
        return {
            str(key): sanitize_trace_payload(
                item,
                blocked_keys=blocked_keys,
                blocked_key_parts=blocked_key_parts,
            )
            for key, item in value.items()
            if not is_blocked_trace_key(
                str(key),
                blocked_keys=blocked_keys,
                blocked_key_parts=blocked_key_parts,
            )
        }
    if isinstance(value, (list, tuple)):
        return [
            sanitize_trace_payload(
                item,
                blocked_keys=blocked_keys,
                blocked_key_parts=blocked_key_parts,
            )
            for item in value
        ]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def is_blocked_trace_key(key: str, *, blocked_keys, blocked_key_parts) -> bool:
    normalized = key.casefold()
    return normalized in blocked_keys or any(
        part in normalized for part in blocked_key_parts
    )


def safe_trace_path_segment(value: str) -> str:
    normalized = "".join(
        character if character.isalnum() or character in "._-" else "_"
        for character in value
    )
    return normalized[:128] or "unknown"
