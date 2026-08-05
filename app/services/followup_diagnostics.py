from __future__ import annotations

import hashlib
import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.services.decision_store import DecisionContract, FollowupPolicyVersion


FOLLOWUP_DIAGNOSTICS_VERSION = "followup-diagnostics-v1"


class FollowupDiagnosticRejected(RuntimeError):
    def __init__(self, reason_code: Literal["session_finished", "stale_command"]):
        super().__init__(reason_code)
        self.reason_code = reason_code


class FollowupPolicySnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_version: FollowupPolicyVersion
    max_followups: int = Field(default=2, ge=0, le=2)
    max_context_chars: int = Field(default=6000, ge=512, le=20000)
    empty_clarification_limit: int = Field(default=1, ge=0, le=1)


class FollowupDiagnosticInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    session_status: Literal["active", "finished"] = "active"
    session_id: str = Field(min_length=1, max_length=128)
    command_expired: bool = False
    skip_command: bool = False
    question_closed: bool = False
    question_id: str = Field(min_length=1, max_length=128)
    question_text: str = Field(min_length=1, max_length=4000)
    focus: str = Field(default="", max_length=1000)
    candidate_answers: list[str] = Field(default_factory=list, max_length=3)
    asked_followups: list[str] = Field(default_factory=list, max_length=2)
    followup_count: int = Field(default=0, ge=0, le=2)
    closed_gap_ids: list[str] = Field(default_factory=list, max_length=16)
    public_knowledge_summary: str = Field(default="", max_length=4000)
    policy: FollowupPolicySnapshot

    @model_validator(mode="after")
    def validate_history(self):
        if self.followup_count != len(self.asked_followups):
            raise ValueError("followup_count must equal asked_followups length")
        if len(self.closed_gap_ids) != len(set(self.closed_gap_ids)):
            raise ValueError("closed_gap_ids must be unique")
        return self


class FollowupDiagnostics(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    diagnostics_version: Literal["followup-diagnostics-v1"] = (
        FOLLOWUP_DIAGNOSTICS_VERSION
    )
    input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    signals: list[Literal["empty", "very_short", "off_topic_candidate"]]
    provider_allowed: bool
    deterministic_decision: DecisionContract | None
    provider_context: dict[str, object]
    forbidden_gap_fingerprints: list[str]
    forbidden_question_fingerprints: list[str]
    context_truncated: bool


def diagnose_followup(value: FollowupDiagnosticInput | dict) -> FollowupDiagnostics:
    request = (
        value
        if isinstance(value, FollowupDiagnosticInput)
        else FollowupDiagnosticInput.model_validate(value)
    )
    if request.session_status == "finished":
        raise FollowupDiagnosticRejected("session_finished")
    if request.command_expired:
        raise FollowupDiagnosticRejected("stale_command")

    signals = _answer_signals(request.candidate_answers)
    decision = _deterministic_decision(request, signals)
    if decision is None:
        context, truncated = _bounded_provider_context(request)
    else:
        # Hard rules must stay independent of Provider prompt construction and
        # its frozen context budget.  In particular, reaching the follow-up
        # limit must always produce a zero-call Decision even when the answer
        # history would not fit the Provider context.
        context, truncated = {}, False
    gap_fingerprints = [_stable_fingerprint(item) for item in request.closed_gap_ids]
    question_fingerprints = [
        _stable_fingerprint(item)
        for item in [request.question_text, *request.asked_followups]
    ]
    return FollowupDiagnostics(
        input_sha256=_input_sha256(request),
        signals=signals,
        provider_allowed=decision is None,
        deterministic_decision=decision,
        provider_context=context,
        forbidden_gap_fingerprints=gap_fingerprints,
        forbidden_question_fingerprints=question_fingerprints,
        context_truncated=truncated,
    )


def _deterministic_decision(
    request: FollowupDiagnosticInput,
    signals: list[str],
) -> DecisionContract | None:
    common = {
        "closed_gap_ids": list(request.closed_gap_ids),
        "policy_version": request.policy.policy_version,
        "decision_confidence": "high",
    }
    if request.policy.policy_version == "fixed_v1":
        if request.followup_count >= min(1, request.policy.max_followups):
            return DecisionContract(
                action="next_question",
                answer_state=_answer_state(signals),
                gap_type="none",
                gap_summary="",
                reason_code="followup_limit_reached",
                **common,
            )
        return DecisionContract(
            action="follow_up",
            answer_state=_answer_state(signals),
            gap_type="clarification",
            gap_summary="请补充一个与当前问题直接相关的关键细节。",
            reason_code="fixed_policy_followup",
            **common,
        )
    if request.followup_count >= request.policy.max_followups:
        return DecisionContract(
            action="next_question",
            answer_state=_answer_state(signals),
            gap_type="none",
            gap_summary="",
            reason_code="followup_limit_reached",
            **common,
        )
    if request.question_closed:
        return DecisionContract(
            action="next_question",
            answer_state="complete",
            gap_type="none",
            gap_summary="",
            reason_code="question_closed",
            **common,
        )
    if request.skip_command:
        return DecisionContract(
            action="next_question",
            answer_state="empty",
            gap_type="none",
            gap_summary="",
            reason_code="skip_command",
            **common,
        )
    if "empty" in signals:
        if request.followup_count < request.policy.empty_clarification_limit:
            return DecisionContract(
                action="follow_up",
                answer_state="empty",
                gap_type="clarification",
                gap_summary="请澄清是否愿意回答当前问题，并补充一个关键事实。",
                reason_code="empty_answer_clarification",
                **common,
            )
        return DecisionContract(
            action="next_question",
            answer_state="empty",
            gap_type="none",
            gap_summary="",
            reason_code="followup_limit_reached",
            **common,
        )
    return None


def _answer_signals(answers: list[str]) -> list[str]:
    latest = answers[-1].strip() if answers else ""
    meaningful = re.sub(r"[\W_]+", "", latest, flags=re.UNICODE)
    signals: list[str] = []
    if not meaningful or latest.casefold() in {
        "不知道",
        "还是不知道",
        "仍然不知道",
        "不会",
        "不清楚",
        "i don't know",
        "still don't know",
        "idk",
        "n/a",
    }:
        signals.append("empty")
    elif len(meaningful) < 12:
        signals.append("very_short")
    normalized = latest.casefold()
    if any(
        marker in normalized
        for marker in ("与问题无关", "没有回答当前问题", "off topic", "does not answer")
    ):
        signals.append("off_topic_candidate")
    return signals


def _answer_state(signals: list[str]) -> str:
    if "empty" in signals:
        return "empty"
    if "off_topic_candidate" in signals:
        return "off_topic"
    return "partial"


def _bounded_provider_context(
    request: FollowupDiagnosticInput,
) -> tuple[dict[str, object], bool]:
    context: dict[str, object] = {
        "question_id": request.question_id,
        "question": request.question_text,
        "focus": request.focus,
        "candidate_answers": list(request.candidate_answers),
        "asked_followups": list(request.asked_followups),
        "followup_count": request.followup_count,
        "closed_gap_fingerprints": [
            _stable_fingerprint(item) for item in request.closed_gap_ids
        ],
        "public_knowledge_summary": request.public_knowledge_summary,
        "policy": request.policy.model_dump(mode="json"),
    }
    encoded = _canonical_json(context)
    if len(encoded) <= request.policy.max_context_chars:
        return context, False

    remaining = request.policy.max_context_chars
    bounded = dict(context)
    for key in ("public_knowledge_summary", "focus", "question"):
        value = str(bounded[key])
        keep = max(0, min(len(value), remaining // 4))
        bounded[key] = value[:keep]
    bounded["candidate_answers"] = [
        answer[-max(64, remaining // 3) :]
        for answer in request.candidate_answers[-2:]
    ]
    while len(_canonical_json(bounded)) > request.policy.max_context_chars:
        answers = list(bounded["candidate_answers"])
        if answers and len(answers[0]) > 64:
            answers[0] = answers[0][len(answers[0]) // 4 :]
            bounded["candidate_answers"] = answers
            continue
        bounded["public_knowledge_summary"] = ""
        if len(_canonical_json(bounded)) <= request.policy.max_context_chars:
            break
        bounded["asked_followups"] = []
        break
    if len(_canonical_json(bounded)) > request.policy.max_context_chars:
        latest_answer = request.candidate_answers[-1] if request.candidate_answers else ""
        bounded = {
            "question_id": request.question_id[:64],
            "question": request.question_text[:128],
            "focus": request.focus[:64],
            "candidate_answers": [latest_answer[-128:]] if latest_answer else [],
            "asked_followups": [],
            "followup_count": request.followup_count,
            "closed_gap_fingerprints": [
                _stable_fingerprint(item) for item in request.closed_gap_ids[-2:]
            ],
            "public_knowledge_summary": "",
            "policy": {
                "policy_version": request.policy.policy_version,
                "max_followups": request.policy.max_followups,
            },
        }
    if len(_canonical_json(bounded)) > request.policy.max_context_chars:
        raise ValueError("follow-up provider context cannot fit the frozen budget")
    return bounded, True


def _input_sha256(request: FollowupDiagnosticInput) -> str:
    return hashlib.sha256(
        _canonical_json(request.model_dump(mode="json")).encode("utf-8")
    ).hexdigest()


def _stable_fingerprint(value: str) -> str:
    normalized = re.sub(r"[\W_]+", "", value.casefold(), flags=re.UNICODE)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
