from __future__ import annotations

import hashlib
import json
from typing import Any

from app.a2a.invocation.context import InvocationContext


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _stable_digest(*parts: Any) -> str:
    return hashlib.sha256(
        _canonical_json(list(parts)).encode("utf-8")
    ).hexdigest()


def build_agent_idempotency_key(
    *,
    agent_id: str,
    skill: str,
    request: dict[str, Any],
    invocation_context: InvocationContext | None = None,
) -> str:
    context = invocation_context or InvocationContext()
    if agent_id == "interview-examiner":
        identity = [
            context.session_id,
            request.get("question_id") or context.question_id,
            context.state_version,
            context.command_id,
            request.get("policy_version"),
        ]
    elif agent_id == "knowledge-and-grounding":
        identity = [
            context.correlation_id,
            request.get("configuration_hash"),
            request.get("knowledge_scope_hash"),
        ]
    elif agent_id == "interview-reviewer":
        identity = [
            context.session_id,
            context.state_version,
            request.get("review_policy_version"),
        ]
    elif agent_id == "report-coach":
        identity = [
            context.session_id,
            request.get("evaluation_set_hash"),
            request.get("report_policy_version"),
        ]
    else:
        identity = [context.session_id, skill, request]
    digest = _stable_digest(*identity)
    return f"a2a-v1:{agent_id}:{skill}:{digest}"
