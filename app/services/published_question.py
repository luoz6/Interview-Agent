from __future__ import annotations

import hashlib
import json
from typing import Any


def question_id(question: Any) -> str:
    value = getattr(question, "id", None) or getattr(
        question, "question_id", None
    )
    if not isinstance(value, str) or not value:
        raise ValueError("question identity is missing")
    return value


def question_kind(question: Any) -> str:
    value = getattr(question, "kind", None) or getattr(
        question, "question_type", None
    )
    if not isinstance(value, str) or not value:
        raise ValueError("question kind is missing")
    return value


def published_question_text(state: dict[str, Any], question: Any) -> str:
    """Resolve the exact interviewer text published for one question.

    V3 intents never contain final wording. Their durable interviewer message is
    written in the same commit as RenderedQuestion and is therefore the public
    store's replay-safe projection of that immutable snapshot.
    """

    target_id = question_id(question)
    if not _is_v3_state(state):
        value = getattr(question, "prompt", None)
        if not isinstance(value, str) or not value.strip():
            raise ValueError("legacy question prompt is missing")
        return value.strip()

    rendered_text = _rendered_text(state, target_id)
    message_text = next(
        (
            str(message.get("content", "")).strip()
            for message in state.get("messages", [])
            if message.get("role") == "interviewer"
            and message.get("question_id") == target_id
            and str(message.get("content", "")).strip()
        ),
        None,
    )
    if rendered_text and message_text and rendered_text != message_text:
        raise ValueError("published question does not match RenderedQuestion")
    if not rendered_text or not message_text:
        raise ValueError(f"V3 question is not published: {target_id}")
    return rendered_text


def published_question_ids(state: dict[str, Any]) -> set[str]:
    """Return committed V3 RenderedQuestion identities.

    ``rendered_question`` is only a generation-stage value. Publication is the
    later atomic commit that adds the snapshot to ``rendered_questions`` and
    writes the interviewer message, so report/review consumers must use the
    committed mapping exclusively.
    """

    if not _is_v3_state(state):
        return {
            question_id(question)
            for question in state["plan"].questions
        }
    rendered_questions = state.get("rendered_questions")
    if not isinstance(rendered_questions, dict):
        return set()
    rendered_ids = {
        target_id
        for target_id in rendered_questions
        if isinstance(target_id, str) and target_id
    }
    message_ids = {
        message.get("question_id")
        for message in state.get("messages", [])
        if message.get("role") == "interviewer"
        and isinstance(message.get("question_id"), str)
        and str(message.get("content", "")).strip()
    }
    return rendered_ids & message_ids


def published_question_lineage(
    state: dict[str, Any], question: Any
) -> dict[str, str | None]:
    """Return hash-only V3 lineage safe for review/evidence projections."""

    target_id = question_id(question)
    if not _is_v3_state(state):
        return {
            "intent_sha256": None,
            "rendered_question_sha256": None,
            "generation_id": None,
        }
    # Reuse the publication consistency check before exposing lineage.
    published_question_text(state, question)
    rendered = (state.get("rendered_questions") or {}).get(target_id)
    if not isinstance(rendered, dict):
        raise ValueError(f"V3 question is not published: {target_id}")
    intent_sha256 = rendered.get("intent_sha256")
    generation_id = rendered.get("generation_id")
    if not isinstance(intent_sha256, str) or len(intent_sha256) != 64:
        raise ValueError("published question intent lineage is missing")
    if not isinstance(generation_id, str) or not generation_id:
        raise ValueError("published question generation lineage is missing")
    canonical = json.dumps(
        rendered,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return {
        "intent_sha256": intent_sha256,
        "rendered_question_sha256": hashlib.sha256(
            canonical.encode("utf-8")
        ).hexdigest(),
        "generation_id": generation_id,
    }


def _rendered_text(state: dict[str, Any], target_id: str) -> str | None:
    rendered_questions = state.get("rendered_questions")
    if not isinstance(rendered_questions, dict):
        return None
    rendered = rendered_questions.get(target_id)
    if not isinstance(rendered, dict) or rendered.get("question_id") != target_id:
        return None
    text = rendered.get("text")
    if isinstance(text, str) and text.strip():
        return text.strip()
    return None


def _is_v3_state(state: dict[str, Any]) -> bool:
    if state.get("workflow_engine") == "langgraph-v3":
        return True
    plan = state.get("plan")
    if getattr(plan, "schema_version", None) == "interview-plan-v3":
        return True
    plan_snapshot = state.get("plan_snapshot")
    return (
        isinstance(plan_snapshot, dict)
        and plan_snapshot.get("schema_version") == "interview-plan-v3"
    )


__all__ = [
    "published_question_ids",
    "published_question_lineage",
    "published_question_text",
    "question_id",
    "question_kind",
]
