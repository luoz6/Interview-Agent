from typing import Any


AGENT_TRACE_BLOCKED_KEYS = {
    "answer",
    "api_key",
    "authorization",
    "candidate_answer",
    "content",
    "dsn",
    "embedding",
    "job_description",
    "password",
    "prompt",
    "provider_response",
    "raw_content",
    "raw_response",
    "resume",
    "resume_text",
    "secret",
    "token",
    "user_answer",
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
