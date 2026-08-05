from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any


CAUSAL_BOUNDARY_VIOLATION = "CAUSAL_BOUNDARY_VIOLATION"
FOLLOWUP_GENERATION_SINK = "followup_generation"
ASSISTANCE_CONTEXT_KIND = "principal_memory_assistance_v1"
ASSISTANCE_LABEL = "Non-authoritative historical preference"
ASSISTANCE_WARNING = (
    "Current-session evidence always wins. Do not use this block for scoring, "
    "evaluation, reporting, hiring decisions, or claims about ability."
)
LOCAL_CONSUME_KEYS = frozenset(
    {
        "interview_language",
        "target_role_family",
        "learning_goal",
    }
)

_MAX_ASSISTANCE_ITEMS = 3
_ITEM_PATTERN = re.compile(
    r"^- category=(?P<key>[a-z_]+); value=(?P<value>[a-z0-9_-]{1,64}); "
    r"authority=(?P<authority>[a-z_]+); confirmation=user_confirmed; "
    r"source_status=available$"
)
_FORBIDDEN_STRUCTURED_FIELDS = frozenset(
    {
        "fact_id",
        "normalized_fact",
        "opaque_deployment_ref",
        "opaque_principal_ref",
        "principal_id",
        "safe_ref",
        "safe_refs",
        "source_artifact_id",
        "source_session_id",
    }
)


class PrincipalMemoryCausalBoundaryViolation(RuntimeError):
    """A Principal Memory value reached a non-allowlisted provider sink."""

    def __init__(self) -> None:
        # Never include the rejected payload in an exception or log message.
        super().__init__(CAUSAL_BOUNDARY_VIOLATION)


def assert_principal_memory_sink(*, operation: str, payload: Any) -> None:
    """Enforce the Local V1 provider-sink boundary on structured payloads.

    Only the follow-up generation adapter may receive one canonical Local
    Consume assistance message. Every sink rejects internal locators. String
    content that is not a structured assistance message is deliberately not
    inspected, so candidate text cannot create a false positive by mentioning
    an implementation term.
    """

    assistance_messages: list[Mapping[str, Any]] = []
    for item in _walk_mappings(payload):
        keys = {str(key).casefold() for key in item}
        if keys & _FORBIDDEN_STRUCTURED_FIELDS:
            raise PrincipalMemoryCausalBoundaryViolation()

        context_kind = item.get("context_kind")
        if context_kind == ASSISTANCE_CONTEXT_KIND:
            assistance_messages.append(item)
        elif isinstance(context_kind, str) and context_kind.startswith(
            "principal_memory_"
        ):
            raise PrincipalMemoryCausalBoundaryViolation()

        if (
            item.get("role") == "system"
            and isinstance(item.get("content"), str)
            and item["content"].startswith(f"[{ASSISTANCE_LABEL}]")
            and context_kind != ASSISTANCE_CONTEXT_KIND
        ):
            raise PrincipalMemoryCausalBoundaryViolation()

    if not assistance_messages:
        return
    if operation != FOLLOWUP_GENERATION_SINK or len(assistance_messages) != 1:
        raise PrincipalMemoryCausalBoundaryViolation()
    _assert_canonical_assistance_message(assistance_messages[0])


def _assert_canonical_assistance_message(message: Mapping[str, Any]) -> None:
    if set(message) != {"role", "content", "context_kind"}:
        raise PrincipalMemoryCausalBoundaryViolation()
    if message.get("role") != "system":
        raise PrincipalMemoryCausalBoundaryViolation()

    content = message.get("content")
    if not isinstance(content, str):
        raise PrincipalMemoryCausalBoundaryViolation()
    lines = content.splitlines()
    if (
        len(lines) < 5
        or lines[0] != f"[{ASSISTANCE_LABEL}]"
        or lines[1] != "Use: local follow-up assistance only."
        or lines[2] != ASSISTANCE_WARNING
        or lines[-1] != f"[/{ASSISTANCE_LABEL}]"
    ):
        raise PrincipalMemoryCausalBoundaryViolation()

    items = lines[3:-1]
    if not 1 <= len(items) <= _MAX_ASSISTANCE_ITEMS:
        raise PrincipalMemoryCausalBoundaryViolation()
    for item in items:
        match = _ITEM_PATTERN.fullmatch(item)
        if match is None or match.group("key") not in LOCAL_CONSUME_KEYS:
            raise PrincipalMemoryCausalBoundaryViolation()


def _walk_mappings(value: Any):
    if isinstance(value, Mapping):
        yield value
        for nested in value.values():
            yield from _walk_mappings(nested)
    elif isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray)
    ):
        for nested in value:
            yield from _walk_mappings(nested)
