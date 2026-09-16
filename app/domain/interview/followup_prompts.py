from __future__ import annotations

import hashlib
import json
import re
from types import MappingProxyType
from typing import Any, Literal, Mapping

from app.domain.interview.decision_store import DecisionContract, GapType

FOLLOWUP_DECISION_PROMPT_VERSION = "followup-decision-v2"
FOLLOWUP_GENERATION_PROMPT_VERSION = "followup-generation-v2"

FollowupDecisionOutputMode = Literal["structured_first", "raw_only"]
RAW_ONLY_FOLLOWUP_DECISION_MODELS = frozenset({"deepseek-v4-pro"})


def fallback_followup(focus: str) -> str:
    return f"请继续深挖 {focus}：你当时做了什么取舍，为什么这样选？"


def resolve_followup_decision_output_mode(
    model: str,
) -> FollowupDecisionOutputMode:
    """Choose one Decision protocol before any Provider request."""

    return (
        "raw_only"
        if model in RAW_ONLY_FOLLOWUP_DECISION_MODELS
        else "structured_first"
    )


_DECISION_RESPONSE_SCHEMA = json.dumps(
    DecisionContract.model_json_schema(),
    ensure_ascii=False,
    sort_keys=True,
    separators=(",", ":"),
)

_DECISION_PROMPT_TEMPLATE = f"""You are a bounded interview follow-up decision engine.
Evaluate only the current main question and the candidate answers supplied in the input.
Return valid JSON only: exactly one object matching DECISION_JSON_SCHEMA.
Do not wrap the JSON in markdown code fences or add text before or after it.
Choose at most one most valuable open gap.
Do not generate a follow-up question, a numeric score, a report, or hidden reasoning.
Do not quote or disclose a reference or ideal answer.
Treat public_knowledge_summary as guidance, never as something the candidate said.
Use next_question for a complete answer, a closed question, a reached follow-up limit,
or when confidence is low. Preserve the supplied policy_version.
DECISION_JSON_SCHEMA={_DECISION_RESPONSE_SCHEMA}
"""

_GENERATION_GAP_GUIDANCE: Mapping[GapType, str] = MappingProxyType(
    {
        "missing_detail": (
            "Ask for one missing implementation detail."
        ),
        "tradeoff": (
            "Ask for one tradeoff and why that choice was made."
        ),
        "failure_mode": (
            "Ask about one failure scenario and its recovery boundary."
        ),
        "evidence": (
            "Ask for one verifiable fact, metric, outcome, or personal contribution."
        ),
        "clarification": (
            "Clarify one ambiguity without opening a new topic."
        ),
        "technical_error": (
            "Ask the candidate to correct one wrong assumption or conclusion."
        ),
        "none": (
            "Generate nothing; continue to the next main question."
        ),
    }
)

_GENERATION_GAP_GUIDANCE_BLOCK = "\n".join(
    f"- {gap_type}: {guidance}"
    for gap_type, guidance in _GENERATION_GAP_GUIDANCE.items()
)
_LEGACY_UNKNOWN_GAP_GUIDANCE = "Ask only about the bounded gap_summary."

_GENERATION_PROMPT_TEMPLATE = """You are a technical interviewer.
Ask exactly one concise follow-up.
Pursue only FOLLOWUP_DECISION_TARGET: {gap_guidance}
One atomic interrogative sentence only: no bundled gaps/subquestions, lists, multiple question marks, or independent asks joined by and/or (also, as well as, "\u540c\u65f6", "\u4ee5\u53ca", "\u53e6\u5916").
Ground it in the latest answer; do not repeat the previous question, main question, or prior follow-up.
Never reveal reference answers, internal gap IDs, confidence, policy, scores, chain-of-thought, or evaluation.
Return only the question without explanation.
Use knowledge_agent entries as interview guidance, not as candidate answers.
Use knowledge_evidence entries only as reference material, never as candidate answers.
Use knowledge_gap entries to focus on the selected missing or incorrect signal.
Do not reveal the complete expected answer or invent claims beyond bound evidence.
"""

_GENERATION_PROMPT_SPEC = (
    f"{_GENERATION_PROMPT_TEMPLATE}\n"
    "GAP_TYPE_GUIDANCE:\n"
    f"{_GENERATION_GAP_GUIDANCE_BLOCK}\n"
    f"LEGACY_UNKNOWN_GUIDANCE: {_LEGACY_UNKNOWN_GAP_GUIDANCE}\n"
)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


FOLLOWUP_DECISION_PROMPT_SHA256 = _sha256(_DECISION_PROMPT_TEMPLATE)
FOLLOWUP_GENERATION_PROMPT_SHA256 = _sha256(_GENERATION_PROMPT_SPEC)


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
    prompt_template = _GENERATION_PROMPT_TEMPLATE.format(
        gap_guidance=_generation_gap_guidance_for_context(context)
    )
    transcript = "\n".join(
        f"{item['role']}: {item['content']}"
        for item in context
        if item.get("content")
    )
    return (
        f"prompt_version={FOLLOWUP_GENERATION_PROMPT_VERSION}\n"
        f"prompt_sha256={FOLLOWUP_GENERATION_PROMPT_SHA256}\n"
        f"{prompt_template}\n"
        f"Recent context:\n{transcript}"
    )


def _generation_gap_guidance_for_context(
    context: list[dict[str, Any]],
) -> str:
    prefix = "[FOLLOWUP_DECISION_TARGET]\n"
    suffix = "\n[/FOLLOWUP_DECISION_TARGET]"
    for item in context:
        content = str(item.get("content", ""))
        if (
            item.get("role") != "system"
            or not content.startswith(prefix)
            or not content.endswith(suffix)
        ):
            continue
        try:
            target = json.loads(content[len(prefix) : -len(suffix)])
        except (TypeError, ValueError):
            continue
        gap_type = target.get("gap_type") if isinstance(target, dict) else None
        if isinstance(gap_type, str):
            return _GENERATION_GAP_GUIDANCE.get(  # type: ignore[arg-type]
                gap_type,
                _LEGACY_UNKNOWN_GAP_GUIDANCE,
            )
    return _LEGACY_UNKNOWN_GAP_GUIDANCE


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
