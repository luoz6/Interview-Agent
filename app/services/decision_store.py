from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from threading import RLock
from typing import Any, Callable, Literal
from uuid import uuid4

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
    provider_invocations: int = Field(default=0, ge=0, le=1)
    created_at: datetime
    updated_at: datetime


class DecisionStoreConflict(RuntimeError):
    pass


class DecisionNotFound(DecisionStoreConflict):
    pass


class InMemoryDecisionStore:
    def __init__(
        self,
        *,
        clock: Callable[[], datetime] | None = None,
        max_attempts: int = 2,
        lease_seconds: int = 60,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self.max_attempts = max_attempts
        self.lease_seconds = lease_seconds
        self._lock = RLock()
        self._decisions: dict[str, DecisionRecord] = {}
        self._keys: dict[tuple[str, str], str] = {}
        self._attempts: dict[str, DecisionAttempt] = {}
        self._attempt_by_decision: dict[str, list[str]] = {}

    def prepare(self, *, session_id: str, source_command_id: str, input_sha256: str) -> DecisionRecord:
        key = (session_id, source_command_id)
        now = self._clock()
        with self._lock:
            existing_id = self._keys.get(key)
            if existing_id:
                existing = self._decisions[existing_id]
                if existing.input_sha256 != input_sha256:
                    raise DecisionStoreConflict("source command input conflicts")
                return deepcopy(existing)
            record = DecisionRecord(
                decision_id=str(uuid4()),
                session_id=session_id,
                source_command_id=source_command_id,
                input_sha256=input_sha256,
                max_attempts=self.max_attempts,
                created_at=now,
                updated_at=now,
            )
            attempt = DecisionAttempt(
                attempt_id=str(uuid4()),
                decision_id=record.decision_id,
                attempt_number=1,
                status="pending",
                fencing_version=0,
                created_at=now,
                updated_at=now,
            )
            self._decisions[record.decision_id] = record
            self._keys[key] = record.decision_id
            self._attempts[attempt.attempt_id] = attempt
            self._attempt_by_decision[record.decision_id] = [attempt.attempt_id]
            return deepcopy(record)

    def get(self, decision_id: str) -> DecisionRecord:
        with self._lock:
            try:
                return deepcopy(self._decisions[decision_id])
            except KeyError as exc:
                raise DecisionNotFound("decision not found") from exc

    def list_attempts(self, decision_id: str) -> list[DecisionAttempt]:
        with self._lock:
            if decision_id not in self._decisions:
                raise DecisionNotFound("decision not found")
            return [deepcopy(self._attempts[item]) for item in self._attempt_by_decision[decision_id]]

    def claim(self, decision_id: str, *, worker_id: str) -> DecisionAttempt:
        now = self._clock()
        with self._lock:
            decision = self._decisions.get(decision_id)
            if decision is None:
                raise DecisionNotFound("decision not found")
            if decision.status in {"completed", "failed"}:
                raise DecisionStoreConflict(
                    f"{decision.status} decision cannot be claimed"
                )
            attempts = [self._attempts[item] for item in self._attempt_by_decision[decision_id]]
            current = attempts[-1]
            if current.status == "running" and current.lease_expires_at and current.lease_expires_at > now:
                raise DecisionStoreConflict("decision attempt is leased")
            if current.status in {"completed", "abandoned"}:
                raise DecisionStoreConflict("decision attempt is terminal")
            if current.attempt_number > decision.max_attempts:
                raise DecisionStoreConflict("decision attempt limit reached")
            updated = current.model_copy(
                update={
                    "status": "running",
                    "lease_owner": worker_id,
                    "lease_token": str(uuid4()),
                    "lease_expires_at": now + timedelta(seconds=self.lease_seconds),
                    "fencing_version": current.fencing_version + 1,
                    "updated_at": now,
                }
            )
            self._attempts[current.attempt_id] = updated
            return deepcopy(updated)

    def heartbeat(self, attempt_id: str, *, worker_id: str, lease_token: str) -> bool:
        now = self._clock()
        with self._lock:
            attempt = self._attempts.get(attempt_id)
            if not attempt or not self._lease_valid(attempt, worker_id, lease_token, now):
                return False
            self._attempts[attempt_id] = attempt.model_copy(
                update={"lease_expires_at": now + timedelta(seconds=self.lease_seconds), "updated_at": now}
            )
            return True

    def complete(
        self,
        attempt_id: str,
        *,
        worker_id: str,
        lease_token: str,
        decision: DecisionContract,
        duration_ms: float | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        provider_invocations: int = 0,
    ) -> DecisionRecord:
        now = self._clock()
        with self._lock:
            attempt = self._attempts.get(attempt_id)
            if attempt is None:
                raise DecisionNotFound("decision attempt not found")
            record = self._decisions[attempt.decision_id]
            if record.status == "completed":
                if record.decision_sha256 != _decision_sha256(decision):
                    raise DecisionStoreConflict("completed decision payload conflicts")
                return deepcopy(record)
            if not self._lease_valid(attempt, worker_id, lease_token, now):
                raise DecisionStoreConflict("decision attempt fencing failed")
            digest = _decision_sha256(decision)
            completed_attempt = attempt.model_copy(
                update={
                    "status": "completed",
                    "lease_owner": None,
                    "lease_token": None,
                    "lease_expires_at": None,
                    "output_sha256": digest,
                    "duration_ms": duration_ms,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "provider_invocations": provider_invocations,
                    "updated_at": now,
                }
            )
            completed_record = record.model_copy(
                update={
                    "status": "completed",
                    "final_decision": decision,
                    "decision_sha256": digest,
                    "updated_at": now,
                }
            )
            self._attempts[attempt_id] = completed_attempt
            self._decisions[record.decision_id] = completed_record
            return deepcopy(completed_record)

    def fail(
        self,
        attempt_id: str,
        *,
        worker_id: str,
        lease_token: str,
        error_code: str,
        duration_ms: float | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        provider_invocations: int = 0,
    ) -> DecisionAttempt:
        now = self._clock()
        with self._lock:
            attempt = self._attempts.get(attempt_id)
            if attempt is None:
                raise DecisionNotFound("decision attempt not found")
            if not self._lease_valid(attempt, worker_id, lease_token, now):
                raise DecisionStoreConflict("decision attempt fencing failed")
            failed = attempt.model_copy(
                update={
                    "status": "failed",
                    "lease_owner": None,
                    "lease_token": None,
                    "lease_expires_at": None,
                    "error_code": error_code,
                    "duration_ms": duration_ms,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "provider_invocations": provider_invocations,
                    "updated_at": now,
                }
            )
            self._attempts[attempt_id] = failed
            if attempt.attempt_number < self._decisions[attempt.decision_id].max_attempts:
                next_attempt = DecisionAttempt(
                    attempt_id=str(uuid4()),
                    decision_id=attempt.decision_id,
                    attempt_number=attempt.attempt_number + 1,
                    status="pending",
                    fencing_version=attempt.fencing_version,
                    created_at=now,
                    updated_at=now,
                )
                self._attempts[next_attempt.attempt_id] = next_attempt
                self._attempt_by_decision[attempt.decision_id].append(next_attempt.attempt_id)
            else:
                record = self._decisions[attempt.decision_id]
                self._decisions[attempt.decision_id] = record.model_copy(
                    update={"status": "failed", "updated_at": now}
                )
            return deepcopy(failed)

    def _lease_valid(self, attempt: DecisionAttempt, worker_id: str, lease_token: str, now: datetime) -> bool:
        return (
            attempt.status == "running"
            and attempt.lease_owner == worker_id
            and attempt.lease_token == lease_token
            and attempt.lease_expires_at is not None
            and attempt.lease_expires_at > now
        )


def _decision_sha256(decision: DecisionContract) -> str:
    encoded = json.dumps(decision.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
