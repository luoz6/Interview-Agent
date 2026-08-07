from __future__ import annotations

import hashlib
import re
from collections import Counter
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.services.decision_store import DecisionContract, InMemoryDecisionStore
from app.services.followup_decision_service import (
    DecisionProviderResult,
    FollowupDecisionExecutionService,
)
from app.services.followup_diagnostics import (
    FollowupDiagnosticInput,
    diagnose_followup,
    is_duplicate_followup_text,
    stable_followup_fingerprint,
)
from app.services.interview_quality_dataset import (
    InterviewQualityCase,
    InterviewQualityDataset,
    canonical_json_bytes,
)
from app.services.interview_quality_gate import (
    GateConfig,
    MetricEvaluation,
    evaluate_metric,
)


ExecutionSource = Literal[
    "deterministic_rule",
    "synthetic_fixture_replay",
    "saved_provider_replay",
    "live_provider",
]
ProviderAttemptKind = Literal["success", "timeout", "invalid_output", "failure"]
GenerationAttemptKind = Literal["success", "timeout", "failure"]


class FollowupEvalAttempt(BaseModel):
    """One normalized fixed/adaptive evaluation attempt.

    ``generated_question`` is the raw Generation output. ``displayed_question``
    is the text that passed runtime safety and was actually eligible for display.
    Quality metrics concerning repetition or leakage intentionally use only the
    latter so a rejected duplicate is not reported as something the user saw.
    """

    model_config = ConfigDict(extra="forbid")

    case_id: str
    partition: Literal["train", "dev", "blind-test"]
    policy_version: Literal["fixed_v1", "adaptive_v1"]
    execution_source: ExecutionSource = "deterministic_rule"
    parsed: bool
    expected_action: Literal["follow_up", "next_question"]
    acceptable_actions: list[Literal["follow_up", "next_question"]]
    predicted_action: Literal["follow_up", "next_question"] | None
    runtime_action: Literal["follow_up", "next_question"] | None = None
    predicted_gap_type: str | None = None
    predicted_gap_summary: str = ""
    decision_reason_code: str | None = None
    decision_confidence: str | None = None
    generated_question: str | None = None
    displayed_question: str | None = None
    generation_rejection_reason: str | None = None
    replay_action: Literal["follow_up", "next_question"] | None = None
    replay_provider_invocations: int = Field(default=0, ge=0)
    followup_count_before: int = Field(default=0, ge=0, le=2)
    followup_count_after: int = Field(default=0, ge=0, le=2)
    terminal_guard_action: Literal["next_question"] | None = None
    terminal_guard_provider_invocations: int | None = Field(default=None, ge=0)
    decision_provider_invocations: int = Field(default=0, ge=0)
    generation_provider_invocations: int = Field(default=0, ge=0)
    provider_invocations: int = Field(default=0, ge=0)
    provider_retries: int = Field(default=0, ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cached_input_tokens: int | None = Field(default=None, ge=0)
    decision_latency_seconds: float = Field(default=0, ge=0)
    generation_complete_latency_seconds: float = Field(default=0, ge=0)
    latency_seconds: float = Field(default=0, ge=0)
    response_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    error_code: str | None = None

    @model_validator(mode="after")
    def validate_runtime_outcome(self):
        expected_total = (
            self.decision_provider_invocations
            + self.generation_provider_invocations
        )
        if self.provider_invocations != expected_total:
            raise ValueError("provider_invocations must equal Decision + Generation")
        if self.displayed_question is not None and self.runtime_action != "follow_up":
            raise ValueError("displayed questions require runtime_action=follow_up")
        if self.generation_rejection_reason and self.displayed_question is not None:
            raise ValueError("rejected Generation output cannot be displayed")
        if self.runtime_action == "follow_up" and not (
            self.displayed_question or self.policy_version == "fixed_v1"
        ):
            raise ValueError("adaptive runtime follow_up requires displayed text")
        expected_after = self.followup_count_before + int(
            self.runtime_action == "follow_up"
        )
        if self.followup_count_after != expected_after:
            raise ValueError("followup_count_after does not match runtime outcome")
        if self.followup_count_after == 2:
            if self.terminal_guard_action != "next_question":
                raise ValueError("two follow-ups require a terminal next guard")
            if self.terminal_guard_provider_invocations != 0:
                raise ValueError("terminal guard after two follow-ups must use zero calls")
        return self


class SavedDecisionAttempt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: ProviderAttemptKind
    payload: dict[str, Any] | None = None
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cached_input_tokens: int | None = Field(default=None, ge=0)
    latency_seconds: float = Field(default=0, ge=0)
    provider_model: str | None = None
    provider_response_id: str | None = None

    @model_validator(mode="after")
    def validate_payload(self):
        if self.kind == "success" and self.payload is None:
            raise ValueError("successful Decision attempts require a payload")
        if self.kind != "success" and self.payload is not None:
            raise ValueError("failed Decision attempts must not carry a payload")
        return self


class SavedGenerationAttempt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: GenerationAttemptKind
    text: str | None = None
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cached_input_tokens: int | None = Field(default=None, ge=0)
    latency_seconds: float = Field(default=0, ge=0)
    provider_model: str | None = None
    provider_response_id: str | None = None

    @model_validator(mode="after")
    def validate_text(self):
        if self.kind == "success" and self.text is None:
            raise ValueError("successful Generation attempts require text")
        if self.kind != "success" and self.text is not None:
            raise ValueError("failed Generation attempts must not carry text")
        return self


class SavedFollowupCaseResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    decision_attempts: list[SavedDecisionAttempt] = Field(default_factory=list)
    generation_attempts: list[SavedGenerationAttempt] = Field(default_factory=list)


class SavedFollowupProviderArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["followup-provider-replay-v1"] = (
        "followup-provider-replay-v1"
    )
    source: Literal["synthetic_fixture", "local_redacted_provider_output"]
    dataset_id: str
    dataset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider_name: str | None = None
    model_id: str | None = None
    capture_status: Literal["complete", "hard_stopped"] = "complete"
    hard_stop_conditions: list[str] = Field(default_factory=list)
    cases: list[SavedFollowupCaseResponse]

    @model_validator(mode="after")
    def validate_cases(self):
        case_ids = [item.case_id for item in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("saved Provider replay case IDs must be unique")
        if self.source == "local_redacted_provider_output" and not (
            self.provider_name and self.model_id
        ):
            raise ValueError("real saved output requires Provider and model identity")
        if self.capture_status == "hard_stopped" and not self.hard_stop_conditions:
            raise ValueError("hard-stopped captures require stop conditions")
        if self.capture_status == "complete" and self.hard_stop_conditions:
            raise ValueError("complete captures cannot carry hard-stop conditions")
        if (
            self.source == "local_redacted_provider_output"
            and self.capture_status == "complete"
        ):
            for case in self.cases:
                for attempt in [
                    *case.decision_attempts,
                    *case.generation_attempts,
                ]:
                    if (
                        attempt.input_tokens is None
                        or attempt.output_tokens is None
                        or attempt.cached_input_tokens is None
                    ):
                        raise ValueError(
                            "complete real saved output requires per-request metering"
                        )
                    if attempt.provider_model != self.model_id:
                        raise ValueError(
                            "saved response model metadata does not match artifact"
                        )
                    if attempt.latency_seconds <= 0:
                        raise ValueError(
                            "complete real saved output requires per-request latency"
                        )
        return self


def calculate_followup_metrics(
    dataset: InterviewQualityDataset,
    attempts: list[FollowupEvalAttempt],
    *,
    gate_config: GateConfig,
) -> dict[str, Any]:
    case_by_id = {case.case_id: case for case in dataset.cases}
    adaptive = [item for item in attempts if item.policy_version == "adaptive_v1"]
    fixed = [item for item in attempts if item.policy_version == "fixed_v1"]
    _validate_attempt_coverage(case_by_id, adaptive, policy_version="adaptive_v1")
    _validate_attempt_coverage(case_by_id, fixed, policy_version="fixed_v1")

    action_hits = sum(
        item.predicted_action in item.acceptable_actions for item in adaptive
    )
    expected_followup = [
        item for item in adaptive if item.expected_action == "follow_up"
    ]
    gap_hits = sum(
        _gap_is_acceptable(case_by_id[item.case_id], item)
        for item in expected_followup
    )
    displayed = [item for item in adaptive if item.displayed_question is not None]
    relevance_hits = sum(
        _question_is_relevant(case_by_id[item.case_id], item)
        for item in displayed
    )
    strong = [
        item
        for item in adaptive
        if "strong_answer" in case_by_id[item.case_id].input["scenario_tags"]
    ]
    unnecessary_strong = sum(
        item.predicted_action == "follow_up" for item in strong
    )
    sequence_results = _sequence_replay_results(dataset, adaptive)
    correction_hits = sum(item["effective_correction"] for item in sequence_results)
    repeated = sum(
        _question_is_repeated(case_by_id[item.case_id], item)
        for item in displayed
    )
    multi_question = sum(
        _is_multi_question(item.displayed_question or "") for item in displayed
    )
    leaks = sum(
        _contains_reference_leak(item.displayed_question or "")
        for item in displayed
    )
    replay_drift = sum(
        item.replay_action is not None
        and item.predicted_action != item.replay_action
        for item in adaptive
    )
    bounded = sum(_attempt_is_bounded(item) for item in adaptive)

    values = {
        "action_accuracy": (_ratio(action_hits, len(adaptive)), len(adaptive)),
        "max_gap_type_accuracy": (
            _ratio(gap_hits, len(expected_followup)),
            len(expected_followup),
        ),
        "latest_answer_relevance_rate": (
            _ratio(relevance_hits, len(expected_followup)),
            len(expected_followup),
        ),
        "unnecessary_followup_rate_strong": (
            _ratio(unnecessary_strong, len(strong)),
            len(strong),
        ),
        "effective_correction_rate": (
            _ratio(correction_hits, len(sequence_results)),
            len(sequence_results),
        ),
        "repeat_original_question_rate": (
            _ratio(repeated, len(expected_followup)),
            len(expected_followup),
        ),
        "multi_question_rate": (
            _ratio(multi_question, len(expected_followup)),
            len(expected_followup),
        ),
        "reference_answer_leak_count": (float(leaks), len(expected_followup)),
        "decision_parse_rate": (
            _ratio(sum(item.parsed for item in adaptive), len(adaptive)),
            len(adaptive),
        ),
        "followup_count_within_zero_to_two_rate": (
            _ratio(bounded, len(adaptive)),
            len(adaptive),
        ),
        "replay_action_drift_count": (float(replay_drift), len(adaptive)),
    }
    evaluations: list[MetricEvaluation] = [
        evaluate_metric(
            gate_config,
            f"followup_quality.{name}",
            actual=actual,
            sample_size=sample_size,
        )
        for name, (actual, sample_size) in values.items()
    ]
    fixed_hits = sum(
        item.predicted_action in item.acceptable_actions for item in fixed
    )
    automated_status = (
        "FAIL"
        if any(item.status == "FAIL" for item in evaluations)
        else "INSUFFICIENT_SAMPLE"
        if any(item.status == "INSUFFICIENT_SAMPLE" for item in evaluations)
        else "PASS"
    )
    pending_review = [
        case.case_id
        for case in dataset.cases
        if case.annotation.review_status != "reviewed" or not case.gate_eligible
    ]
    quality_status = (
        "FAIL_AUTOMATED"
        if automated_status == "FAIL"
        else "BLOCKED_INSUFFICIENT_SAMPLE"
        if automated_status == "INSUFFICIENT_SAMPLE"
        else "BLOCKED_PENDING_INDEPENDENT_REVIEW"
        if pending_review
        else "PASS"
    )
    raw_duplicate_rejections = [
        item.case_id
        for item in adaptive
        if item.generation_rejection_reason == "duplicate_question"
    ]
    partition_comparison = {
        partition: _partition_comparison(adaptive, fixed, partition)
        for partition in ("train", "dev", "blind-test")
    }
    return {
        "dataset_case_count": len(dataset.cases),
        "adaptive_attempt_count": len(adaptive),
        "fixed_attempt_count": len(fixed),
        "fixed_action_accuracy": _ratio(fixed_hits, len(fixed)),
        "adaptive_action_accuracy": values["action_accuracy"][0],
        "partition_action_comparison": partition_comparison,
        "provider_invocations": sum(item.provider_invocations for item in adaptive),
        "provider_retries": sum(item.provider_retries for item in adaptive),
        "input_tokens": sum(item.input_tokens or 0 for item in adaptive),
        "output_tokens": sum(item.output_tokens or 0 for item in adaptive),
        "cached_input_tokens": sum(
            item.cached_input_tokens or 0 for item in adaptive
        ),
        "decision_latency_seconds": sum(
            item.decision_latency_seconds for item in adaptive
        ),
        "generation_complete_latency_seconds": sum(
            item.generation_complete_latency_seconds for item in adaptive
        ),
        "total_latency_seconds": sum(item.latency_seconds for item in adaptive),
        "parse_failures": [item.case_id for item in adaptive if not item.parsed],
        "error_counts": dict(
            Counter(item.error_code for item in adaptive if item.error_code)
        ),
        "displayed_followup_count": len(displayed),
        "rejected_generation_count": sum(
            item.generation_rejection_reason is not None for item in adaptive
        ),
        "raw_duplicate_rejection_case_ids": raw_duplicate_rejections,
        "user_visible_duplicate_count": repeated,
        "sequence_replay": {
            "sequence_count": len(sequence_results),
            "effective_correction_count": correction_hits,
            "terminal_zero_call_checks": sum(
                item["terminal_guard_checked"] for item in sequence_results
            ),
            "terminal_zero_call_passes": sum(
                item["terminal_guard_passed"] for item in sequence_results
            ),
            "results": sequence_results,
        },
        "metric_evaluations": [
            item.model_dump(mode="json") for item in evaluations
        ],
        "automated_status": automated_status,
        "independent_review_status": (
            "PENDING" if pending_review else "COMPLETE"
        ),
        "pending_independent_review_case_count": len(pending_review),
        "quality_status": quality_status,
    }


def fixed_policy_attempts(
    dataset: InterviewQualityDataset,
) -> list[FollowupEvalAttempt]:
    attempts: list[FollowupEvalAttempt] = []
    for case in dataset.cases:
        request = _diagnostic_request(case, policy_version="fixed_v1")
        diagnostics = diagnose_followup(request)
        decision = diagnostics.deterministic_decision
        if decision is None:  # pragma: no cover - fixed_v1 is server-owned.
            raise AssertionError("fixed_v1 must be deterministic")
        runtime_action = decision.action
        after = request.followup_count + int(runtime_action == "follow_up")
        terminal_action, terminal_calls = _terminal_guard(
            request,
            decision,
            displayed_question="fixed policy follow-up",
            followup_count_after=after,
        )
        attempts.append(
            FollowupEvalAttempt(
                case_id=case.case_id,
                partition=case.partition,
                policy_version="fixed_v1",
                execution_source="deterministic_rule",
                parsed=True,
                expected_action=case.expectation.action,
                acceptable_actions=case.expectation.acceptable_actions,
                predicted_action=decision.action,
                runtime_action=runtime_action,
                predicted_gap_type=decision.gap_type,
                predicted_gap_summary=decision.gap_summary,
                decision_reason_code=decision.reason_code,
                decision_confidence=decision.decision_confidence,
                replay_action=decision.action,
                followup_count_before=request.followup_count,
                followup_count_after=after,
                terminal_guard_action=terminal_action,
                terminal_guard_provider_invocations=terminal_calls,
            )
        )
    return attempts


def build_synthetic_fixture_replay(
    dataset: InterviewQualityDataset,
    *,
    dataset_sha256: str,
) -> SavedFollowupProviderArtifact:
    """Build deterministic non-Provider fixtures for offline harness coverage.

    The artifact is deliberately labelled ``synthetic_fixture``. It proves
    parser, policy, retry, replay and sequence behavior but must never be
    represented as a real Provider quality result.
    """

    cases: list[SavedFollowupCaseResponse] = []
    for case in dataset.cases:
        provider_mode = str(case.input["provider_fixture"]["mode"])
        decision_attempts: list[SavedDecisionAttempt]
        if provider_mode == "normal":
            decision_attempts = [
                SavedDecisionAttempt(
                    kind="success",
                    payload=_fixture_decision(case).model_dump(mode="json"),
                )
            ]
        elif provider_mode == "low_confidence":
            decision_attempts = [
                SavedDecisionAttempt(
                    kind="success",
                    payload=_fixture_low_confidence_decision(case).model_dump(
                        mode="json"
                    ),
                )
            ]
        else:
            kind = {
                "provider_timeout": "timeout",
                "provider_invalid_output": "invalid_output",
                "provider_failed": "failure",
            }[provider_mode]
            decision_attempts = [SavedDecisionAttempt(kind=kind) for _ in range(2)]

        generation_mode = str(case.input["generation_fixture"]["mode"])
        if generation_mode == "repeat_main_question":
            generation_attempts = [
                SavedGenerationAttempt(
                    kind="success", text=str(case.input["question_text"])
                )
                for _ in range(3)
            ]
        else:
            generation_attempts = [
                SavedGenerationAttempt(
                    kind="success", text=_fixture_generation_text(case)
                )
            ]
        cases.append(
            SavedFollowupCaseResponse(
                case_id=case.case_id,
                decision_attempts=decision_attempts,
                generation_attempts=generation_attempts,
            )
        )
    return SavedFollowupProviderArtifact(
        source="synthetic_fixture",
        dataset_id=dataset.dataset_id,
        dataset_sha256=dataset_sha256,
        cases=cases,
    )


def replay_saved_provider_artifact(
    dataset: InterviewQualityDataset,
    artifact: SavedFollowupProviderArtifact,
    *,
    dataset_sha256: str,
) -> list[FollowupEvalAttempt]:
    if artifact.capture_status != "complete":
        raise ValueError("hard-stopped Provider captures cannot be replayed as complete")
    if artifact.dataset_id != dataset.dataset_id:
        raise ValueError("saved Provider replay dataset ID mismatch")
    if artifact.dataset_sha256 != dataset_sha256:
        raise ValueError("saved Provider replay dataset hash mismatch")
    response_by_id = {item.case_id: item for item in artifact.cases}
    expected_ids = {case.case_id for case in dataset.cases}
    if set(response_by_id) != expected_ids:
        raise ValueError("saved Provider replay must cover the selected dataset exactly")
    source: ExecutionSource = (
        "synthetic_fixture_replay"
        if artifact.source == "synthetic_fixture"
        else "saved_provider_replay"
    )
    return [
        _replay_case(case, response_by_id[case.case_id], execution_source=source)
        for case in dataset.cases
    ]


def load_saved_provider_artifact(path) -> SavedFollowupProviderArtifact:
    return SavedFollowupProviderArtifact.model_validate_json(
        path.read_text(encoding="utf-8")
    )


def _replay_case(
    case: InterviewQualityCase,
    saved: SavedFollowupCaseResponse,
    *,
    execution_source: ExecutionSource,
) -> FollowupEvalAttempt:
    request = _diagnostic_request(case, policy_version="adaptive_v1")
    decision_provider = _SavedDecisionProvider(saved.decision_attempts)
    store = InMemoryDecisionStore(max_attempts=2)
    service = FollowupDecisionExecutionService(
        store=store,
        provider=decision_provider,
    )
    source_command_id = f"eval:{case.case_id}"
    result = service.execute(
        request,
        source_command_id=source_command_id,
        worker_id="followup-eval",
    )
    if result.decision is None:  # pragma: no cover - in-memory execution is sync.
        raise AssertionError("evaluation Decision did not complete synchronously")
    replay = service.execute(
        request,
        source_command_id=source_command_id,
        worker_id="followup-eval-replay",
    )
    decision = result.decision
    generation = _run_saved_generation(
        case,
        decision,
        saved.generation_attempts,
    )
    runtime_action = generation["runtime_action"]
    before = request.followup_count
    after = before + int(runtime_action == "follow_up")
    terminal_action, terminal_calls = _terminal_guard(
        request,
        decision,
        displayed_question=generation["displayed_question"],
        followup_count_after=after,
    )
    response_payload = {
        "decision": decision.model_dump(mode="json"),
        "generated_question": generation["generated_question"],
        "displayed_question": generation["displayed_question"],
        "generation_rejection_reason": generation["rejection_reason"],
    }
    decision_calls = result.provider_invocations
    generation_calls = int(generation["provider_invocations"])
    total_calls = decision_calls + generation_calls
    provider_retries = max(0, decision_calls - int(decision_calls > 0)) + max(
        0, generation_calls - int(generation_calls > 0)
    )
    decision_latency = sum(
        item.latency_seconds for item in saved.decision_attempts[:decision_calls]
    )
    generation_latency = sum(
        item.latency_seconds
        for item in saved.generation_attempts[:generation_calls]
    )
    input_tokens = _optional_sum(
        decision_provider.input_tokens,
        generation["input_tokens"],
    )
    output_tokens = _optional_sum(
        decision_provider.output_tokens,
        generation["output_tokens"],
    )
    cached_input_tokens = _optional_sum(
        decision_provider.cached_input_tokens,
        generation["cached_input_tokens"],
    )
    return FollowupEvalAttempt(
        case_id=case.case_id,
        partition=case.partition,
        policy_version="adaptive_v1",
        execution_source=execution_source,
        parsed=not any(
            item.kind == "invalid_output" for item in saved.decision_attempts
        ),
        expected_action=case.expectation.action,
        acceptable_actions=case.expectation.acceptable_actions,
        predicted_action=decision.action,
        runtime_action=runtime_action,
        predicted_gap_type=decision.gap_type,
        predicted_gap_summary=decision.gap_summary,
        decision_reason_code=decision.reason_code,
        decision_confidence=decision.decision_confidence,
        generated_question=generation["generated_question"],
        displayed_question=generation["displayed_question"],
        generation_rejection_reason=generation["rejection_reason"],
        replay_action=replay.decision.action if replay.decision else None,
        replay_provider_invocations=replay.provider_invocations,
        followup_count_before=before,
        followup_count_after=after,
        terminal_guard_action=terminal_action,
        terminal_guard_provider_invocations=terminal_calls,
        decision_provider_invocations=decision_calls,
        generation_provider_invocations=generation_calls,
        provider_invocations=total_calls,
        provider_retries=provider_retries,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_input_tokens=cached_input_tokens,
        decision_latency_seconds=decision_latency,
        generation_complete_latency_seconds=generation_latency,
        latency_seconds=decision_latency + generation_latency,
        response_sha256=hashlib.sha256(
            canonical_json_bytes(response_payload)
        ).hexdigest(),
        error_code=(
            decision.reason_code
            if decision.reason_code
            in {"provider_timeout", "provider_invalid_output", "provider_failed"}
            else generation["rejection_reason"]
        ),
    )


class _SavedDecisionProvider:
    def __init__(self, attempts: list[SavedDecisionAttempt]) -> None:
        self.attempts = list(attempts)
        self.input_tokens: int | None = None
        self.output_tokens: int | None = None
        self.cached_input_tokens: int | None = None

    def __call__(self, context: dict[str, object]):
        del context
        if not self.attempts:
            raise RuntimeError("saved Decision attempts exhausted")
        attempt = self.attempts.pop(0)
        self.input_tokens = _optional_sum(self.input_tokens, attempt.input_tokens)
        self.output_tokens = _optional_sum(self.output_tokens, attempt.output_tokens)
        self.cached_input_tokens = _optional_sum(
            self.cached_input_tokens,
            attempt.cached_input_tokens,
        )
        if attempt.kind == "timeout":
            raise TimeoutError("saved Provider timeout")
        if attempt.kind == "failure":
            raise RuntimeError("saved Provider failure")
        if attempt.kind == "invalid_output":
            return {"not": "a DecisionContract"}
        assert attempt.payload is not None
        return DecisionProviderResult(
            decision=DecisionContract.model_validate(attempt.payload),
            input_tokens=attempt.input_tokens,
            output_tokens=attempt.output_tokens,
            cached_input_tokens=attempt.cached_input_tokens,
        )


def _run_saved_generation(
    case: InterviewQualityCase,
    decision: DecisionContract,
    attempts: list[SavedGenerationAttempt],
) -> dict[str, Any]:
    if decision.action != "follow_up":
        return {
            "runtime_action": "next_question",
            "generated_question": None,
            "displayed_question": None,
            "rejection_reason": None,
            "provider_invocations": 0,
            "input_tokens": None,
            "output_tokens": None,
            "cached_input_tokens": None,
        }
    queue = list(attempts)
    raw_text: str | None = None
    rejection_reason: str | None = None
    calls = 0
    input_values: list[int] = []
    output_values: list[int] = []
    cached_values: list[int] = []
    for _ in range(3):
        calls += 1
        if not queue:
            rejection_reason = "generation_retry_exhausted"
            break
        attempt = queue.pop(0)
        if attempt.input_tokens is not None:
            input_values.append(attempt.input_tokens)
        if attempt.output_tokens is not None:
            output_values.append(attempt.output_tokens)
        if attempt.cached_input_tokens is not None:
            cached_values.append(attempt.cached_input_tokens)
        if attempt.kind != "success":
            rejection_reason = (
                "provider_timeout" if attempt.kind == "timeout" else "provider_failed"
            )
            continue
        raw_text = (attempt.text or "").strip()
        if not raw_text:
            rejection_reason = "provider_invalid_output"
            continue
        if is_duplicate_followup_text(
            raw_text,
            [case.input["question_text"], *case.input["asked_followups"]],
        ):
            rejection_reason = "duplicate_question"
            continue
        return {
            "runtime_action": "follow_up",
            "generated_question": raw_text,
            "displayed_question": raw_text,
            "rejection_reason": None,
            "provider_invocations": calls,
            "input_tokens": sum(input_values) if input_values else None,
            "output_tokens": sum(output_values) if output_values else None,
            "cached_input_tokens": sum(cached_values) if cached_values else None,
        }
    return {
        "runtime_action": "next_question",
        "generated_question": raw_text,
        "displayed_question": None,
        "rejection_reason": rejection_reason or "generation_retry_exhausted",
        "provider_invocations": calls,
        "input_tokens": sum(input_values) if input_values else None,
        "output_tokens": sum(output_values) if output_values else None,
        "cached_input_tokens": sum(cached_values) if cached_values else None,
    }


def _terminal_guard(
    request: FollowupDiagnosticInput,
    decision: DecisionContract,
    *,
    displayed_question: str | None,
    followup_count_after: int,
) -> tuple[Literal["next_question"] | None, int | None]:
    if followup_count_after != 2:
        return None, None
    question = displayed_question or "bounded second follow-up"
    terminal = request.model_copy(
        update={
            "asked_followups": [*request.asked_followups, question],
            "followup_count": 2,
            "open_gap_id": (
                stable_followup_fingerprint(decision.gap_summary)
                if decision.action == "follow_up"
                else request.open_gap_id
            ),
        }
    )
    diagnostics = diagnose_followup(terminal)
    terminal_decision = diagnostics.deterministic_decision
    if diagnostics.provider_allowed or terminal_decision is None:
        raise AssertionError("two-follow-up terminal guard attempted Provider work")
    if terminal_decision.action != "next_question":
        raise AssertionError("two-follow-up terminal guard did not advance")
    return "next_question", 0


def _diagnostic_request(
    case: InterviewQualityCase,
    *,
    policy_version: Literal["fixed_v1", "adaptive_v1"],
) -> FollowupDiagnosticInput:
    allowed = set(FollowupDiagnosticInput.model_fields)
    payload = {key: value for key, value in case.input.items() if key in allowed}
    policy = dict(payload.get("policy") or {})
    policy["policy_version"] = policy_version
    payload["policy"] = policy
    payload["session_id"] = f"eval-{case.case_id}"
    return FollowupDiagnosticInput.model_validate(payload)


def _fixture_decision(case: InterviewQualityCase) -> DecisionContract:
    action = case.expectation.action
    gap = (
        case.expectation.acceptable_gaps[0]
        if case.expectation.acceptable_gaps
        else {"gap_type": "none", "summary": ""}
    )
    return DecisionContract(
        action=action,
        answer_state=_answer_state_for_case(case),
        gap_type=(str(gap["gap_type"]) if action == "follow_up" else "none"),
        gap_summary=(str(gap["summary"]) if action == "follow_up" else ""),
        reason_code=case.expectation.expected_reason_codes[0],
        decision_confidence="high",
        closed_gap_ids=(
            [str(case.input["open_gap_id"])]
            if action == "follow_up" and case.input.get("open_gap_id")
            else []
        ),
        policy_version="adaptive_v1",
    )


def _fixture_low_confidence_decision(case: InterviewQualityCase) -> DecisionContract:
    gap = (
        case.expectation.acceptable_gaps[0]
        if case.expectation.acceptable_gaps
        else {"gap_type": "clarification", "summary": "one bounded clarification"}
    )
    return DecisionContract(
        action="follow_up",
        answer_state=_answer_state_for_case(case),
        gap_type=str(gap["gap_type"]),
        gap_summary=str(gap["summary"]),
        reason_code="missing_detail",
        decision_confidence="low",
        closed_gap_ids=[],
        policy_version="adaptive_v1",
    )


def _fixture_generation_text(case: InterviewQualityCase) -> str:
    if case.expectation.action != "follow_up":
        return "unused generation fixture"
    gap = case.expectation.acceptable_gaps[0]
    summary = str(gap["summary"])
    if case.language == "en":
        return f"Could you clarify {summary}?"
    return f"请具体说明{summary}？"


def _answer_state_for_case(case: InterviewQualityCase) -> str:
    return {
        "strong": "complete",
        "medium": "partial",
        "partial": "partial",
        "incorrect": "incorrect",
        "off_topic": "off_topic",
        "empty": "empty",
        "not_applicable": "partial",
    }[case.quality_label]


def _validate_attempt_coverage(
    case_by_id: dict[str, InterviewQualityCase],
    attempts: list[FollowupEvalAttempt],
    *,
    policy_version: str,
) -> None:
    ids = [item.case_id for item in attempts]
    if set(ids) != set(case_by_id):
        raise ValueError(f"{policy_version} attempts must cover every case exactly once")
    if len(ids) != len(set(ids)):
        raise ValueError(f"{policy_version} attempts contain duplicate case IDs")
    for item in attempts:
        case = case_by_id[item.case_id]
        if item.partition != case.partition:
            raise ValueError(f"partition mismatch for {item.case_id}")
        if item.expected_action != case.expectation.action:
            raise ValueError(f"expected action mismatch for {item.case_id}")


def _sequence_replay_results(
    dataset: InterviewQualityDataset,
    adaptive: list[FollowupEvalAttempt],
) -> list[dict[str, Any]]:
    attempt_by_id = {item.case_id: item for item in adaptive}
    grouped: dict[str, list[tuple[int, InterviewQualityCase]]] = {}
    for case in dataset.cases:
        sequence_id = case.input.get("sequence_id")
        if sequence_id is None:
            continue
        grouped.setdefault(str(sequence_id), []).append(
            (int(case.input["sequence_step"]), case)
        )
    results: list[dict[str, Any]] = []
    for sequence_id, members in sorted(grouped.items()):
        ordered = [case for _, case in sorted(members)]
        if len(ordered) != 2:
            raise ValueError(f"incomplete sequence: {sequence_id}")
        first = attempt_by_id[ordered[0].case_id]
        second = attempt_by_id[ordered[1].case_id]
        second_gap_ok = (
            second.predicted_action != "follow_up"
            or _gap_is_acceptable(ordered[1], second)
        )
        correction = bool(
            first.runtime_action == "follow_up"
            and second.predicted_action in second.acceptable_actions
            and second_gap_ok
            and second.followup_count_after <= 2
            and (
                second.runtime_action != "follow_up"
                or second.displayed_question is not None
            )
        )
        terminal_checked = second.followup_count_after == 2
        terminal_passed = bool(
            terminal_checked
            and second.terminal_guard_action == "next_question"
            and second.terminal_guard_provider_invocations == 0
        )
        results.append(
            {
                "sequence_id": sequence_id,
                "partition": ordered[0].partition,
                "step_actions": [first.predicted_action, second.predicted_action],
                "runtime_actions": [first.runtime_action, second.runtime_action],
                "final_followup_count": second.followup_count_after,
                "effective_correction": correction,
                "terminal_guard_checked": terminal_checked,
                "terminal_guard_passed": terminal_passed,
            }
        )
    return results


def _partition_comparison(
    adaptive: list[FollowupEvalAttempt],
    fixed: list[FollowupEvalAttempt],
    partition: str,
) -> dict[str, float | int]:
    adaptive_items = [item for item in adaptive if item.partition == partition]
    fixed_items = [item for item in fixed if item.partition == partition]
    adaptive_hits = sum(
        item.predicted_action in item.acceptable_actions for item in adaptive_items
    )
    fixed_hits = sum(
        item.predicted_action in item.acceptable_actions for item in fixed_items
    )
    return {
        "case_count": len(adaptive_items),
        "fixed_action_accuracy": _ratio(fixed_hits, len(fixed_items)),
        "adaptive_action_accuracy": _ratio(adaptive_hits, len(adaptive_items)),
    }


def _gap_is_acceptable(
    case: InterviewQualityCase,
    attempt: FollowupEvalAttempt,
) -> bool:
    if attempt.predicted_action != "follow_up":
        return False
    allowed_types = {
        str(item.get("gap_type")) for item in case.expectation.acceptable_gaps
    }
    if attempt.predicted_gap_type not in allowed_types:
        return False
    normalized = attempt.predicted_gap_summary.casefold()
    return not any(
        forbidden.casefold() in normalized
        for forbidden in case.expectation.forbidden_gaps
        if forbidden.strip()
    )


def _question_is_relevant(
    case: InterviewQualityCase,
    attempt: FollowupEvalAttempt,
) -> bool:
    question = attempt.displayed_question or ""
    if not question.strip() or _question_is_repeated(case, attempt):
        return False
    question_units = _semantic_units(question)
    latest_units = _semantic_units(str(case.input["candidate_answers"][-1]))
    gap_units = _semantic_units(attempt.predicted_gap_summary)
    expected_units = set()
    for gap in case.expectation.acceptable_gaps:
        for keyword in gap.get("required_keywords", []):
            expected_units.update(_semantic_units(str(keyword)))
    return bool(question_units & (latest_units | gap_units | expected_units))


def _question_is_repeated(
    case: InterviewQualityCase,
    attempt: FollowupEvalAttempt,
) -> bool:
    return is_duplicate_followup_text(
        attempt.displayed_question or "",
        [case.input["question_text"], *case.input["asked_followups"]],
    )


def _attempt_is_bounded(attempt: FollowupEvalAttempt) -> bool:
    if not 0 <= attempt.followup_count_before <= attempt.followup_count_after <= 2:
        return False
    if attempt.followup_count_before == 2:
        return (
            attempt.runtime_action == "next_question"
            and attempt.provider_invocations == 0
        )
    if attempt.followup_count_after == 2:
        return (
            attempt.terminal_guard_action == "next_question"
            and attempt.terminal_guard_provider_invocations == 0
        )
    return True


def _semantic_units(value: str) -> set[str]:
    folded = value.casefold()
    words = set(re.findall(r"[a-z0-9]{3,}", folded))
    chinese = "".join(re.findall(r"[\u4e00-\u9fff]", folded))
    words.update(
        chinese[index : index + 2]
        for index in range(max(0, len(chinese) - 1))
    )
    return words


def _is_multi_question(value: str) -> bool:
    return value.count("?") + value.count("？") > 1


def _contains_reference_leak(value: str) -> bool:
    normalized = value.casefold()
    return any(
        marker in normalized
        for marker in (
            "reference answer",
            "ideal answer",
            "标准答案",
            "参考答案",
            "gap_id",
            "gap_type",
            "decision_confidence",
            "policy_version",
            "chain-of-thought",
        )
    )


def _optional_sum(*values: object) -> int | None:
    present = [value for value in values if isinstance(value, int)]
    return sum(present) if present else None


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0
