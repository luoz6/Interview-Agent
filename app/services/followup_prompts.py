from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

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


_UNSAFE_FOLLOWUP_OUTPUT_MARKERS = (
    "reference answer",
    "ideal answer",
    "system prompt",
    "developer prompt",
    "hidden prompt",
    "chain-of-thought",
    "ignore previous",
    "ignore all previous",
    "prompt_version=",
    "prompt_sha256=",
    "followup_decision_target",
    "decision_confidence",
    "policy_version",
    "gap_id",
    "gap_type",
    "参考答案",
    "标准答案",
    "系统提示词",
    "开发者提示词",
    "忽略之前",
)


class UnsafeFollowupOutput(ValueError):
    """Raised before an untrusted follow-up can be persisted or displayed."""


def validate_followup_output(
    text: str,
    context: list[dict[str, str]],
) -> str:
    value = str(text or "").strip()
    if not value:
        raise UnsafeFollowupOutput("empty follow-up output")
    normalized = _fold_security_text(value)
    if any(marker in normalized for marker in _UNSAFE_FOLLOWUP_OUTPUT_MARKERS):
        raise UnsafeFollowupOutput("follow-up output contains an internal marker")
    for item in context:
        if str(item.get("role", "")).casefold() not in {
            "system",
            "developer",
            "knowledge_agent",
            "knowledge_evidence",
            "reference",
        }:
            continue
        protected = _fold_security_text(str(item.get("content", "")))
        if _contains_protected_excerpt(normalized, protected):
            raise UnsafeFollowupOutput(
                "follow-up output contains protected context text"
            )
    return value


def _fold_security_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.casefold()).strip()


def _contains_protected_excerpt(output: str, protected: str) -> bool:
    compact_output = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", output)
    compact_protected = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", protected)
    window = 18
    if len(compact_protected) < window or len(compact_output) < window:
        return False
    return any(
        compact_protected[index : index + window] in compact_output
        for index in range(len(compact_protected) - window + 1)
    )


class StructuredFollowupOutputError(ValueError):
    """A Provider response arrived but did not satisfy DecisionContract."""

    def __init__(self, message: str, *, response: object) -> None:
        super().__init__(message)
        usage = getattr(response, "usage_metadata", None) or {}
        self.input_tokens = _usage_int(usage, "input_tokens")
        self.output_tokens = _usage_int(usage, "output_tokens")
        self.cached_input_tokens = _cached_input_tokens(usage)
        self.provider_model = _provider_model(response)
        self.provider_response_id = _provider_response_id(response)


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

    def __init__(self, chat_model, *, max_tokens: int = 300) -> None:
        self.chat_model = chat_model
        self.max_tokens = max_tokens

    def __call__(self, context: dict[str, object]):
        from app.services.followup_decision_service import DecisionProviderResult

        prompt = render_followup_decision_prompt(context)
        try:
            structured = self.chat_model.with_structured_output(
                DecisionContract,
                method="json_schema",
                include_raw=True,
            )
        except TypeError:
            # Lightweight test doubles and older LangChain adapters may not
            # expose include_raw. Production uses it so Provider usage remains
            # measurable without placing token fields in DecisionContract.
            structured = self.chat_model.with_structured_output(
                DecisionContract,
                method="json_schema",
            )
        if hasattr(structured, "bind"):
            structured = structured.bind(max_tokens=self.max_tokens)
        begin_provider_attempt()
        result = structured.invoke(prompt)
        raw = result.get("raw") if isinstance(result, dict) else None
        parsed = (
            result.get("parsed")
            if isinstance(result, dict) and "parsed" in result
            else result
        )
        publish_provider_response(raw or result)
        if isinstance(result, dict) and result.get("parsing_error") is not None:
            raise StructuredFollowupOutputError(
                "Provider Decision response failed structured parsing",
                response=raw or result,
            )
        if parsed is None:
            raise StructuredFollowupOutputError(
                "Provider Decision response did not contain a parsed value",
                response=raw or result,
            )
        decision = (
            parsed
            if isinstance(parsed, DecisionContract)
            else DecisionContract.model_validate(parsed)
        )
        usage = getattr(raw or result, "usage_metadata", None) or {}
        return DecisionProviderResult(
            decision=decision,
            input_tokens=_usage_int(usage, "input_tokens"),
            output_tokens=_usage_int(usage, "output_tokens"),
            cached_input_tokens=_cached_input_tokens(usage),
            provider_model=_provider_model(raw or result),
            provider_response_id=_provider_response_id(raw or result),
        )


class FollowupGenerationProviderResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cached_input_tokens: int | None = Field(default=None, ge=0)
    provider_model: str | None = None
    provider_response_id: str | None = None


class StructuredFollowupGenerationProvider:
    prompt_version = FOLLOWUP_GENERATION_PROMPT_VERSION
    prompt_sha256 = FOLLOWUP_GENERATION_PROMPT_SHA256

    def __init__(self, chat_model, *, max_tokens: int = 120) -> None:
        self.chat_model = chat_model
        self.max_tokens = max_tokens

    def __call__(
        self,
        context: list[dict[str, Any]],
    ) -> FollowupGenerationProviderResult:
        prompt = render_followup_generation_prompt(context)
        model = self.chat_model
        if hasattr(model, "bind"):
            model = model.bind(max_tokens=self.max_tokens)
        begin_provider_attempt()
        response = model.invoke(prompt)
        publish_provider_response(response)
        usage = getattr(response, "usage_metadata", None) or {}
        content = getattr(response, "content", response)
        return FollowupGenerationProviderResult(
            text=str(content or "").strip(),
            input_tokens=_usage_int(usage, "input_tokens"),
            output_tokens=_usage_int(usage, "output_tokens"),
            cached_input_tokens=_cached_input_tokens(usage),
            provider_model=_provider_model(response),
            provider_response_id=_provider_response_id(response),
        )


def _usage_int(usage: object, key: str) -> int | None:
    if not isinstance(usage, dict):
        return None
    value = usage.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _cached_input_tokens(usage: object) -> int | None:
    if not isinstance(usage, dict):
        return None
    details = usage.get("input_token_details")
    if not isinstance(details, dict):
        return None
    for key in ("cache_read", "cached_tokens"):
        value = details.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
    return None


def _provider_model(response: object) -> str | None:
    metadata = getattr(response, "response_metadata", None)
    if not isinstance(metadata, dict):
        return None
    value = metadata.get("model_name") or metadata.get("model")
    return value if isinstance(value, str) and value else None


def _provider_response_id(response: object) -> str | None:
    value = getattr(response, "id", None)
    return value if isinstance(value, str) and value else None
