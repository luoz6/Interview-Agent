from __future__ import annotations

import hashlib
from time import monotonic
from typing import Callable, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.services.decision_store import (
    DecisionContract,
    DecisionRecord,
    DecisionStoreConflict,
)
from app.services.followup_diagnostics import (
    FollowupDiagnosticInput,
    FollowupDiagnostics,
    diagnose_followup,
    stable_followup_fingerprint,
)
from app.services.followup_prompts import (
    FOLLOWUP_DECISION_PROMPT_SHA256,
    FOLLOWUP_DECISION_PROMPT_VERSION,
)


class FollowupDecisionProvider(Protocol):
    def __call__(self, context: dict[str, object]) -> object: ...


class _DecisionProviderFailure(RuntimeError):
    def __init__(
        self,
        error_code: Literal[
            "provider_timeout", "provider_invalid_output", "provider_failed"
        ],
        *,
        source: object | None = None,
    ) -> None:
        super().__init__(error_code)
        self.error_code = error_code
        self.input_tokens = _safe_usage_int(source, "input_tokens")
        self.output_tokens = _safe_usage_int(source, "output_tokens")
        cached_input_tokens = _safe_usage_int(source, "cached_input_tokens")
        self.cached_input_tokens = (
            None
            if (
                cached_input_tokens is not None
                and self.input_tokens is not None
                and cached_input_tokens > self.input_tokens
            )
            else cached_input_tokens
        )
        response_id = _safe_usage_text(source, "provider_response_id")
        self.provider_response_id_sha256 = _sha256_optional_text(response_id)


class DecisionProviderResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    decision: DecisionContract
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cached_input_tokens: int | None = Field(default=None, ge=0)
    provider_model: str | None = None
    provider_response_id: str | None = None

    @model_validator(mode="after")
    def validate_cached_usage(self):
        if (
            self.cached_input_tokens is not None
            and self.input_tokens is not None
            and self.cached_input_tokens > self.input_tokens
        ):
            raise ValueError("cached input tokens cannot exceed input tokens")
        return self


class DecisionExecutionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["accepted", "completed"]
    decision_id: str
    attempt_number: int | None
    decision: DecisionContract | None
    replayed: bool
    provider_invocations: int = Field(ge=0)
    duration_ms: float = Field(ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cached_input_tokens: int | None = Field(default=None, ge=0)
    provider_response_id_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )


class FollowupDecisionExecutionService:
    def __init__(
        self,
        *,
        store,
        provider: FollowupDecisionProvider | None,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self.store = store
        self.provider = provider
        self.clock = clock
        self.decision_prompt_version = getattr(
            provider,
            "prompt_version",
            FOLLOWUP_DECISION_PROMPT_VERSION,
        )
        self.decision_prompt_sha256 = getattr(
            provider,
            "prompt_sha256",
            FOLLOWUP_DECISION_PROMPT_SHA256,
        )

    def execute(
        self,
        request: FollowupDiagnosticInput | dict,
        *,
        source_command_id: str,
        worker_id: str,
    ) -> DecisionExecutionResult:
        started = self.clock()
        diagnostics = diagnose_followup(request)
        typed_request = (
            request
            if isinstance(request, FollowupDiagnosticInput)
            else FollowupDiagnosticInput.model_validate(request)
        )
        record = self.store.prepare(
            session_id=typed_request.session_id,
            source_command_id=source_command_id,
            input_sha256=diagnostics.input_sha256,
            decision_prompt_version=self.decision_prompt_version,
            decision_prompt_sha256=self.decision_prompt_sha256,
        )
        if record.status == "completed":
            return self._completed_result(
                record,
                started=started,
                replayed=True,
                provider_invocations=0,
            )

        provider_invocations = 0
        while True:
            attempt_started = self.clock()
            attempt_provider_invocations = 0
            input_tokens = output_tokens = cached_input_tokens = None
            provider_response_id_sha256 = None
            try:
                attempt = self.store.claim(record.decision_id, worker_id=worker_id)
            except DecisionStoreConflict as exc:
                current = self.store.get(record.decision_id)
                if current.status == "completed":
                    return self._completed_result(
                        current,
                        started=started,
                        replayed=True,
                        provider_invocations=provider_invocations,
                    )
                if "leased" in str(exc):
                    return DecisionExecutionResult(
                        status="accepted",
                        decision_id=record.decision_id,
                        attempt_number=None,
                        decision=None,
                        replayed=False,
                        provider_invocations=provider_invocations,
                        duration_ms=_duration_ms(self.clock(), started),
                    )
                raise

            try:
                if diagnostics.deterministic_decision is not None:
                    decision = diagnostics.deterministic_decision
                else:
                    provider_invocations += 1
                    attempt_provider_invocations = 1
                    provider_result = self._invoke_provider(
                        diagnostics,
                        request=typed_request,
                    )
                    input_tokens = provider_result.input_tokens
                    output_tokens = provider_result.output_tokens
                    cached_input_tokens = provider_result.cached_input_tokens
                    provider_response_id_sha256 = _sha256_optional_text(
                        provider_result.provider_response_id
                    )
                    decision = provider_result.decision
            except _DecisionProviderFailure as failure:
                error_code = failure.error_code
                input_tokens = failure.input_tokens
                output_tokens = failure.output_tokens
                cached_input_tokens = failure.cached_input_tokens
                provider_response_id_sha256 = (
                    failure.provider_response_id_sha256
                )
            else:
                completed = self.store.complete(
                    attempt.attempt_id,
                    worker_id=worker_id,
                    lease_token=attempt.lease_token,
                    decision=decision,
                    duration_ms=_duration_ms(self.clock(), attempt_started),
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cached_input_tokens=cached_input_tokens,
                    provider_response_id_sha256=provider_response_id_sha256,
                    provider_invocations=attempt_provider_invocations,
                )
                return self._completed_result(
                    completed,
                    started=started,
                    replayed=False,
                    provider_invocations=provider_invocations,
                    attempt_number=attempt.attempt_number,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cached_input_tokens=cached_input_tokens,
                    provider_response_id_sha256=provider_response_id_sha256,
                )

            if attempt.attempt_number < record.max_attempts:
                self.store.fail(
                    attempt.attempt_id,
                    worker_id=worker_id,
                    lease_token=attempt.lease_token,
                    error_code=error_code,
                    duration_ms=_duration_ms(self.clock(), attempt_started),
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cached_input_tokens=cached_input_tokens,
                    provider_response_id_sha256=provider_response_id_sha256,
                    provider_invocations=attempt_provider_invocations,
                )
                record = self.store.get(record.decision_id)
                continue

            fallback = _fallback_decision(
                typed_request,
                diagnostics,
                reason_code=error_code,
            )
            completed = self.store.complete(
                attempt.attempt_id,
                worker_id=worker_id,
                lease_token=attempt.lease_token,
                decision=fallback,
                duration_ms=_duration_ms(self.clock(), attempt_started),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cached_input_tokens=cached_input_tokens,
                provider_response_id_sha256=provider_response_id_sha256,
                provider_invocations=attempt_provider_invocations,
            )
            return self._completed_result(
                completed,
                started=started,
                replayed=False,
                provider_invocations=provider_invocations,
                attempt_number=attempt.attempt_number,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cached_input_tokens=cached_input_tokens,
                provider_response_id_sha256=provider_response_id_sha256,
            )

    def _invoke_provider(
        self,
        diagnostics: FollowupDiagnostics,
        *,
        request: FollowupDiagnosticInput,
    ) -> DecisionProviderResult:
        if self.provider is None:
            raise _DecisionProviderFailure("provider_failed")
        try:
            raw = self.provider(dict(diagnostics.provider_context))
        except TimeoutError as exc:
            raise _DecisionProviderFailure("provider_timeout", source=exc) from exc
        except (ValidationError, ValueError, TypeError) as exc:
            # Structured adapters may validate before returning to this
            # service. Such responses are malformed Provider output, not a
            # transport outage, and retain the stable invalid-output code.
            raise _DecisionProviderFailure(
                "provider_invalid_output", source=exc
            ) from exc
        except Exception as exc:
            raise _DecisionProviderFailure("provider_failed", source=exc) from exc
        try:
            result = _parse_provider_result(raw)
            return result.model_copy(
                update={
                    "decision": _enforce_runtime_policy(
                        result.decision,
                        request=request,
                        diagnostics=diagnostics,
                    )
                }
            )
        except (ValidationError, ValueError, TypeError) as exc:
            raise _DecisionProviderFailure(
                "provider_invalid_output", source=raw
            ) from exc

    def _completed_result(
        self,
        record: DecisionRecord,
        *,
        started: float,
        replayed: bool,
        provider_invocations: int,
        attempt_number: int | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        cached_input_tokens: int | None = None,
        provider_response_id_sha256: str | None = None,
    ) -> DecisionExecutionResult:
        return DecisionExecutionResult(
            status="completed",
            decision_id=record.decision_id,
            attempt_number=attempt_number,
            decision=record.final_decision,
            replayed=replayed,
            provider_invocations=provider_invocations,
            duration_ms=_duration_ms(self.clock(), started),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_input_tokens=cached_input_tokens,
            provider_response_id_sha256=provider_response_id_sha256,
        )


def _parse_provider_result(raw: object) -> DecisionProviderResult:
    if isinstance(raw, DecisionProviderResult):
        return raw
    if isinstance(raw, DecisionContract):
        return DecisionProviderResult(decision=raw)
    if isinstance(raw, dict) and "decision" in raw:
        return DecisionProviderResult.model_validate(raw)
    return DecisionProviderResult(
        decision=DecisionContract.model_validate(raw)
    )


def _sha256_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _safe_usage_int(source: object | None, name: str) -> int | None:
    value = (
        source.get(name)
        if isinstance(source, dict)
        else getattr(source, name, None)
    )
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _safe_usage_text(source: object | None, name: str) -> str | None:
    value = (
        source.get(name)
        if isinstance(source, dict)
        else getattr(source, name, None)
    )
    return value if isinstance(value, str) and value else None


def _enforce_runtime_policy(
    decision: DecisionContract,
    *,
    request: FollowupDiagnosticInput,
    diagnostics: FollowupDiagnostics,
) -> DecisionContract:
    if decision.policy_version != request.policy.policy_version:
        raise ValueError("decision policy version mismatch")
    allowed_closed_gap_ids = set(request.closed_gap_ids)
    if request.open_gap_id is not None:
        allowed_closed_gap_ids.add(request.open_gap_id)
    if any(
        item not in allowed_closed_gap_ids for item in decision.closed_gap_ids
    ):
        raise ValueError("Provider returned an unknown closed gap identifier")
    closed_gap_ids = list(
        dict.fromkeys([*request.closed_gap_ids, *decision.closed_gap_ids])
    )
    decision = decision.model_copy(update={"closed_gap_ids": closed_gap_ids})
    if decision.action == "follow_up":
        new_gap_id = stable_followup_fingerprint(decision.gap_summary)
        forbidden_gap_ids = set(diagnostics.forbidden_gap_fingerprints)
        if request.open_gap_id is not None:
            forbidden_gap_ids.add(request.open_gap_id)
        if new_gap_id in forbidden_gap_ids:
            if request.open_gap_id is not None:
                closed_gap_ids = list(
                    dict.fromkeys([*closed_gap_ids, request.open_gap_id])
                )
            return DecisionContract(
                action="next_question",
                answer_state=decision.answer_state,
                gap_type="none",
                gap_summary="",
                reason_code="duplicate_gap",
                decision_confidence=decision.decision_confidence,
                closed_gap_ids=closed_gap_ids,
                policy_version=decision.policy_version,
            )
        if request.open_gap_id is not None:
            decision = decision.model_copy(
                update={
                    "closed_gap_ids": list(
                        dict.fromkeys(
                            [*decision.closed_gap_ids, request.open_gap_id]
                        )
                    )
                }
            )
    if decision.action == "follow_up" and decision.decision_confidence == "low":
        return DecisionContract(
            action="next_question",
            answer_state=decision.answer_state,
            gap_type="none",
            gap_summary="",
            reason_code="low_confidence",
            decision_confidence="low",
            closed_gap_ids=decision.closed_gap_ids,
            policy_version=decision.policy_version,
        )
    return decision


def _fallback_decision(
    request: FollowupDiagnosticInput,
    diagnostics: FollowupDiagnostics,
    *,
    reason_code: Literal[
        "provider_timeout", "provider_invalid_output", "provider_failed"
    ],
) -> DecisionContract:
    # Exhausted Provider failures always fail closed.  An off-topic answer is
    # not a reason to create one more model-dependent Generation after the
    # Decision Provider has already failed repeatedly.
    return DecisionContract(
        action="next_question",
        answer_state=(
            "empty"
            if "empty" in diagnostics.signals
            else "off_topic"
            if "off_topic_candidate" in diagnostics.signals
            else "partial"
        ),
        gap_type="none",
        gap_summary="",
        reason_code=reason_code,
        decision_confidence="low",
        closed_gap_ids=request.closed_gap_ids,
        policy_version=request.policy.policy_version,
    )


def _duration_ms(ended: float, started: float) -> float:
    return max(0.0, (ended - started) * 1000)
