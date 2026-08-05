from __future__ import annotations

import hashlib
import json
from typing import Any

from app.services.decision_store import DecisionContract
from app.services.provider_usage import (
    begin_provider_attempt,
    publish_provider_response,
)


FOLLOWUP_DECISION_PROMPT_VERSION = "followup-decision-v1"
FOLLOWUP_GENERATION_PROMPT_VERSION = "followup-generation-v1"

_DECISION_PROMPT_TEMPLATE = """You are a bounded interview follow-up decision engine.
Evaluate only the current main question and the candidate answers supplied in the input.
Return exactly one object matching the FollowupDecision schema.
Choose at most one most valuable open gap.
Do not generate a follow-up question, a numeric score, a report, or hidden reasoning.
Do not quote or disclose a reference or ideal answer.
Treat public_knowledge_summary as guidance, never as something the candidate said.
Use next_question for a complete answer, a closed question, a reached follow-up limit,
or when confidence is low. Preserve the supplied policy_version.
"""

_GENERATION_PROMPT_TEMPLATE = """You are a professional technical interviewer.
Ask exactly one concise follow-up question for the current question.
Use the server-owned FOLLOWUP_DECISION_TARGET as the only gap to pursue.
The question must be grounded in the candidate's latest answer, independently answerable,
and must not repeat the main question or an earlier follow-up.
Do not reveal a reference answer, internal gap identifier, confidence, policy, score,
chain-of-thought, or evaluation. Return only the question without explanation.
Use knowledge_agent entries as interview guidance, not as candidate answers.
Use knowledge_evidence entries only as reference material, never as candidate answers.
"""


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


FOLLOWUP_DECISION_PROMPT_SHA256 = _sha256(_DECISION_PROMPT_TEMPLATE)
FOLLOWUP_GENERATION_PROMPT_SHA256 = _sha256(_GENERATION_PROMPT_TEMPLATE)


def render_followup_decision_prompt(context: dict[str, object]) -> str:
    return (
        f"prompt_version={FOLLOWUP_DECISION_PROMPT_VERSION}\n"
        f"prompt_sha256={FOLLOWUP_DECISION_PROMPT_SHA256}\n"
        f"{_DECISION_PROMPT_TEMPLATE}\n"
        "CURRENT_QUESTION_INPUT_JSON:\n"
        + json.dumps(
            context,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )


def render_followup_generation_prompt(
    context: list[dict[str, Any]],
) -> str:
    transcript = "\n".join(
        f"{item['role']}: {item['content']}"
        for item in context
        if item.get("content")
    )
    return (
        f"prompt_version={FOLLOWUP_GENERATION_PROMPT_VERSION}\n"
        f"prompt_sha256={FOLLOWUP_GENERATION_PROMPT_SHA256}\n"
        f"{_GENERATION_PROMPT_TEMPLATE}\n"
        f"Recent context:\n{transcript}"
    )


def generation_context_for_decision(
    context: list[dict[str, Any]],
    decision: DecisionContract,
) -> list[dict[str, Any]]:
    if decision.action != "follow_up":
        raise ValueError("generation context requires a follow-up Decision")
    return generation_context_for_target(
        context,
        gap_type=decision.gap_type,
        gap_summary=decision.gap_summary,
    )


def generation_context_for_target(
    context: list[dict[str, Any]],
    *,
    gap_type: str,
    gap_summary: str,
) -> list[dict[str, Any]]:
    if not gap_summary.strip() or gap_type == "none":
        raise ValueError("generation target requires one bounded open gap")
    target = {
        "gap_type": gap_type,
        "gap_summary": gap_summary,
    }
    instruction = {
        "role": "system",
        "content": (
            "[FOLLOWUP_DECISION_TARGET]\n"
            + json.dumps(
                target,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n[/FOLLOWUP_DECISION_TARGET]"
        ),
    }
    return [instruction, *[dict(item) for item in context]]


class StructuredFollowupDecisionProvider:
    prompt_version = FOLLOWUP_DECISION_PROMPT_VERSION
    prompt_sha256 = FOLLOWUP_DECISION_PROMPT_SHA256

    def __init__(self, chat_model, *, max_tokens: int = 500) -> None:
        self.chat_model = chat_model
        self.max_tokens = max_tokens

    def __call__(self, context: dict[str, object]):
        from app.services.followup_decision_service import DecisionProviderResult

        prompt = render_followup_decision_prompt(context)
        structured = self.chat_model.with_structured_output(
            DecisionContract,
            method="json_schema",
        )
        if hasattr(structured, "bind"):
            structured = structured.bind(max_tokens=self.max_tokens)
        begin_provider_attempt()
        result = structured.invoke(prompt)
        publish_provider_response(result)
        decision = (
            result
            if isinstance(result, DecisionContract)
            else DecisionContract.model_validate(result)
        )
        usage = getattr(result, "usage_metadata", None) or {}
        return DecisionProviderResult(
            decision=decision,
            input_tokens=_usage_int(usage, "input_tokens"),
            output_tokens=_usage_int(usage, "output_tokens"),
        )


def _usage_int(usage: object, key: str) -> int | None:
    if not isinstance(usage, dict):
        return None
    value = usage.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value
