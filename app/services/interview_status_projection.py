from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from typing import Any, Literal

from app.services.question_memory_index import (
    QUESTION_MEMORY_UNRESOLVED_TOPIC_CODES,
)


INTERVIEW_STATUS_SCHEMA_VERSION = "interview-semantic-status-v1"
INTERVIEW_STATUS_ROLE = "interview_semantic_status"
MAX_ACTIVE_FOCUS_TAGS = 8
MAX_ADVISORY_UNRESOLVED_TOPIC_CODES = 2

INTERVIEW_STATUS_ADVISORY_TOPIC_CODES = frozenset(
    QUESTION_MEMORY_UNRESOLVED_TOPIC_CODES
)
_FIELDS = frozenset(
    {
        "schema_version",
        "plan_question_count",
        "current_question_index",
        "completed_question_count",
        "reviewed_question_count",
        "active_focus_tags",
        "advisory_unresolved_topic_codes",
    }
)
_FOCUS_TAXONOMY = (
    ("api design", "api_design"),
    ("cache", "cache_consistency"),
    ("distributed", "distributed_systems"),
    ("failure", "failure_handling"),
    ("idempoten", "idempotency"),
    ("observability", "observability"),
    ("performance", "performance"),
    ("reliability", "reliability"),
    ("security", "security"),
    ("system", "system_design"),
    ("test", "testing"),
)
INTERVIEW_STATUS_FOCUS_TAGS = frozenset(
    tag for _needle, tag in _FOCUS_TAXONOMY
)


def resolve_status_projection_mode(
    *,
    status_projection_enabled: bool,
    compression_mode: str,
) -> Literal["disabled", "shadow", "consume"]:
    if compression_mode not in {"disabled", "shadow", "consume"}:
        raise ValueError("status projection compression mode is invalid")
    if not status_projection_enabled or compression_mode == "disabled":
        return "disabled"
    return compression_mode


def build_interview_status_projection(
    state: Mapping[str, Any],
    *,
    review_records: Iterable[Any] = (),
    advisory_unresolved_topic_codes: Iterable[Any] = (),
) -> dict[str, Any]:
    plan = state.get("plan_snapshot")
    if not isinstance(plan, Mapping):
        raise ValueError("interview status plan snapshot is invalid")
    questions = plan.get("questions")
    if not isinstance(questions, list):
        raise ValueError("interview status plan questions are invalid")
    current_index = state.get("current_index")
    if (
        isinstance(current_index, bool)
        or not isinstance(current_index, int)
        or current_index < 0
        or current_index > len(questions)
    ):
        raise ValueError("interview status current index is invalid")

    session_id = state.get("session_id")
    completed_question_ids = {
        question.get("id")
        for question in questions[:current_index]
        if isinstance(question, Mapping)
        and isinstance(question.get("id"), str)
        and question.get("id")
    }
    reviewed_question_ids = {
        question_id
        for record in review_records
        if _record_value(record, "session_id") == session_id
        and _record_value(record, "status") == "completed"
        and (question_id := _record_value(record, "question_id"))
        in completed_question_ids
    }
    active_focus_tags = (
        _active_focus_tags(questions[current_index])
        if current_index < len(questions)
        else []
    )
    advisory_codes = sorted(
        {
            value
            for value in advisory_unresolved_topic_codes
            if isinstance(value, str)
            and value in INTERVIEW_STATUS_ADVISORY_TOPIC_CODES
        }
    )[:MAX_ADVISORY_UNRESOLVED_TOPIC_CODES]
    return {
        "schema_version": INTERVIEW_STATUS_SCHEMA_VERSION,
        "plan_question_count": len(questions),
        "current_question_index": current_index,
        "completed_question_count": current_index,
        "reviewed_question_count": len(reviewed_question_ids),
        "active_focus_tags": active_focus_tags,
        "advisory_unresolved_topic_codes": advisory_codes,
    }


def render_interview_status_message(
    projection: Mapping[str, Any],
) -> dict[str, str]:
    payload = dict(projection)
    if not _valid_projection_payload(payload):
        raise ValueError("interview semantic status projection is invalid")
    return {
        "role": INTERVIEW_STATUS_ROLE,
        "content": json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ),
    }


def is_valid_interview_status_message(message: Mapping[str, Any]) -> bool:
    if set(message) != {"role", "content"}:
        return False
    if message.get("role") != INTERVIEW_STATUS_ROLE:
        return False
    content = message.get("content")
    if not isinstance(content, str):
        return False
    try:
        payload = json.loads(content)
    except (TypeError, ValueError):
        return False
    return isinstance(payload, dict) and _valid_projection_payload(payload)


def _record_value(record: Any, name: str) -> Any:
    if isinstance(record, Mapping):
        return record.get(name)
    return getattr(record, name, None)


def _active_focus_tags(question: Any) -> list[str]:
    if not isinstance(question, Mapping):
        return []
    text = f"{question.get('kind', '')} {question.get('focus', '')}".casefold()
    return sorted(
        {
            tag
            for needle, tag in _FOCUS_TAXONOMY
            if needle in text and tag in INTERVIEW_STATUS_FOCUS_TAGS
        }
    )[:MAX_ACTIVE_FOCUS_TAGS]


def _valid_projection_payload(payload: Mapping[str, Any]) -> bool:
    if set(payload) != _FIELDS:
        return False
    if payload.get("schema_version") != INTERVIEW_STATUS_SCHEMA_VERSION:
        return False
    counts = (
        payload.get("plan_question_count"),
        payload.get("current_question_index"),
        payload.get("completed_question_count"),
        payload.get("reviewed_question_count"),
    )
    if any(isinstance(value, bool) or not isinstance(value, int) for value in counts):
        return False
    plan_count, current_index, completed_count, reviewed_count = counts
    if not (
        plan_count >= 0
        and 0 <= current_index <= plan_count
        and completed_count == current_index
        and 0 <= reviewed_count <= completed_count
    ):
        return False
    focus = payload.get("active_focus_tags")
    advisory = payload.get("advisory_unresolved_topic_codes")
    if not isinstance(focus, list) or not isinstance(advisory, list):
        return False
    if (
        len(focus) > MAX_ACTIVE_FOCUS_TAGS
        or len(focus) != len(set(focus))
        or focus != sorted(focus)
        or any(value not in INTERVIEW_STATUS_FOCUS_TAGS for value in focus)
    ):
        return False
    return not (
        len(advisory) > MAX_ADVISORY_UNRESOLVED_TOPIC_CODES
        or len(advisory) != len(set(advisory))
        or advisory != sorted(advisory)
        or any(
            value not in INTERVIEW_STATUS_ADVISORY_TOPIC_CODES
            for value in advisory
        )
    )
