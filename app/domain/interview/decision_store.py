from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

DecisionAction = Literal["follow_up", "next_question"]
AnswerState = Literal["complete", "partial", "incorrect", "off_topic", "empty"]
GapType = Literal[
    "missing_detail",
    "tradeoff",
    "failure_mode",
    "evidence",
    "clarification",
    "technical_error",
    "none",
]
DecisionReasonCode = Literal[
    "answer_complete",
    "missing_detail",
    "missing_tradeoff",
    "missing_failure_mode",
    "missing_evidence",
    "clarification_needed",
    "technical_error",
    "off_topic",
    "empty_answer_clarification",
    "fixed_policy_followup",
    "followup_limit_reached",
    "question_closed",
    "skip_command",
    "session_finished",
    "stale_command",
    "duplicate_gap",
    "duplicate_question",
    "repeated_state",
    "provider_call_limit_reached",
    "generation_retry_exhausted",
    "event_limit_reached",
    "node_step_limit_reached",
    "checkpoint_stalled",
    "followup_progress_stalled",
    "low_confidence",
    "provider_timeout",
    "provider_invalid_output",
    "provider_failed",
]
FollowupPolicyVersion = Literal["fixed_v1", "adaptive_v1"]
DecisionStatus = Literal["pending", "completed", "failed"]
AttemptStatus = Literal["pending", "running", "completed", "failed", "abandoned"]


class DecisionContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    action: DecisionAction
    answer_state: AnswerState
    gap_type: GapType
    gap_summary: str = Field(max_length=240)
    reason_code: DecisionReasonCode
    decision_confidence: Literal["high", "medium", "low"]
    closed_gap_ids: list[str] = Field(default_factory=list, max_length=16)
    policy_version: FollowupPolicyVersion

    @model_validator(mode="after")
    def validate_contract(self) -> "DecisionContract":
        if self.action == "next_question" and self.gap_type != "none":
            raise ValueError("next_question decisions must not carry an open gap")
        if self.action == "next_question" and self.gap_summary:
            raise ValueError("next_question decisions must not carry a gap summary")
        if self.action == "follow_up" and self.gap_type == "none":
            raise ValueError("follow_up decisions require one open gap")
        if self.action == "follow_up" and not self.gap_summary.strip():
            raise ValueError("follow_up decisions require a gap summary")
        if "\n" in self.gap_summary:
            raise ValueError("gap_summary must be a single-line diagnostic")
        normalized_summary = self.gap_summary.casefold()
        if any(
            marker in normalized_summary
            for marker in ("标准答案", "参考答案全文", "reference answer", "ideal answer")
        ):
            raise ValueError("gap_summary must not disclose a reference answer")
        if len(self.closed_gap_ids) != len(set(self.closed_gap_ids)):
            raise ValueError("closed_gap_ids must be unique")
        if any(not item.strip() or len(item) > 100 for item in self.closed_gap_ids):
            raise ValueError("closed_gap_ids must be non-empty stable identifiers")
        return self


class DecisionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    decision_id: str
    session_id: str
    source_command_id: str
    input_sha256: str
    decision_prompt_version: str | None = None
    decision_prompt_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    max_attempts: int = Field(ge=1)
    status: DecisionStatus = "pending"
    final_decision: DecisionContract | None = None
    decision_sha256: str | None = None
    created_at: datetime
    updated_at: datetime


class DecisionAttempt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    attempt_id: str
    decision_id: str
    attempt_number: int = Field(ge=1)
    status: AttemptStatus
    lease_owner: str | None = None
    lease_token: str | None = None
    lease_expires_at: datetime | None = None
    fencing_version: int = Field(ge=0)
    error_code: str | None = None
    output_sha256: str | None = None
    duration_ms: float | None = Field(default=None, ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cached_input_tokens: int | None = Field(default=None, ge=0)
    provider_response_id_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    provider_invocations: int = Field(default=0, ge=0, le=1)
    created_at: datetime
    updated_at: datetime


class DecisionStoreConflict(RuntimeError):
    pass


class DecisionNotFound(DecisionStoreConflict):
    pass


def _decision_sha256(decision: DecisionContract) -> str:
    encoded = json.dumps(decision.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _validate_decision_attempt_usage(
    *,
    input_tokens: int | None,
    cached_input_tokens: int | None,
    provider_response_id_sha256: str | None,
) -> None:
    if (
        input_tokens is not None
        and cached_input_tokens is not None
        and cached_input_tokens > input_tokens
    ):
        raise ValueError("cached input tokens cannot exceed input tokens")
    if (
        provider_response_id_sha256 is not None
        and re.fullmatch(r"[0-9a-f]{64}", provider_response_id_sha256) is None
    ):
        raise ValueError("Provider response trace must be a lowercase SHA-256")
