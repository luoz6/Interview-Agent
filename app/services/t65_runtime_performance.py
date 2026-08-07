from __future__ import annotations

from collections import Counter
from typing import Annotated, Any, Literal, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.services.agent_runtime import AgentRunRecord
from app.services.followup_performance import (
    FollowupPerformanceArtifact,
    FollowupPerformanceSample,
    PerformancePricingSnapshot,
    SseRecoveryMeasurement,
    evaluate_followup_performance,
)
from app.services.interview_quality_gate import GateConfig
from app.services.t65_provider_evidence import (
    PerformanceSignal,
    T65PerformanceObservability,
)
from app.services.trace_sanitization import sanitize_agent_safe_metadata


SafeIdentifier = Annotated[
    str,
    Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:@+\-]+$"),
]
Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
GitObject = Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]


class RuntimeCohortSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    cold_or_warm: Literal["cold", "warm"]
    fixed_or_adaptive: Literal["fixed_v1", "adaptive_v1"]
    followup_or_next_question: Literal["follow_up", "next_question"]
    first_or_recovery: Literal["first", "recovery"]
    schema_version: SafeIdentifier
    question_count: int = Field(ge=1)
    provider_path: SafeIdentifier
    target_samples: int = Field(ge=1)

    def identity(self) -> tuple[str, str, str, str, str, int, str]:
        return (
            self.cold_or_warm,
            self.fixed_or_adaptive,
            self.followup_or_next_question,
            self.first_or_recovery,
            self.schema_version,
            self.question_count,
            self.provider_path,
        )


class T65RuntimeCapturePlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["t65-runtime-capture-plan-v1"] = (
        "t65-runtime-capture-plan-v1"
    )
    plan_id: SafeIdentifier
    candidate_revision: GitObject
    candidate_tree: GitObject
    gate_config_sha256: Sha256
    authorization_sha256: Sha256
    cohorts: list[RuntimeCohortSpec] = Field(min_length=1)
    report_baseline_artifact: str | None = None
    report_baseline_artifact_sha256: Sha256 | None = None

    @model_validator(mode="after")
    def validate_plan_contract(self):
        identities = [cohort.identity() for cohort in self.cohorts]
        if len(identities) != len(set(identities)):
            raise ValueError("runtime capture cohorts must be unique")
        if (self.report_baseline_artifact is None) != (
            self.report_baseline_artifact_sha256 is None
        ):
            raise ValueError("report baseline path and hash must be provided together")
        return self


class CapturedTimingBoundaries(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    sample_id: SafeIdentifier
    session_id: SafeIdentifier
    question_id: SafeIdentifier
    command_id: SafeIdentifier
    cohort: RuntimeCohortSpec
    capture_complete: bool = True
    followup_count_before: int = Field(default=0, ge=0, le=2)
    decision_complete_seconds: float | None = Field(default=None, ge=0)
    provider_first_item_seconds: float | None = Field(default=None, ge=0)
    followup_first_visible_seconds: float | None = Field(default=None, ge=0)
    generation_complete_seconds: float | None = Field(default=None, ge=0)
    next_question_visible_seconds: float | None = Field(default=None, ge=0)
    sse_resume_seconds: float | None = Field(default=None, ge=0)
    turn_complete_seconds: float | None = Field(default=None, ge=0)
    provider_attempts: int | None = Field(default=None, ge=0)
    provider_metered_attempts: int | None = Field(default=None, ge=0)
    retries: int | None = Field(default=None, ge=0)
    # The authorization manifest forbids automatic or alternate-model fallback,
    # so zero is a proven policy invariant rather than an unknown measurement.
    fallback_count: int | None = Field(default=0, ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cached_input_tokens: int | None = Field(default=None, ge=0)
    decision_output_tokens: int | None = Field(default=None, ge=0)
    followup_output_tokens: int | None = Field(default=None, ge=0)
    decision_degraded: bool = False
    provider_trace_id_sha256s: list[Sha256] = Field(default_factory=list)
    timing_sources: dict[str, SafeIdentifier] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_capture(self):
        usage_values = (
            self.provider_attempts,
            self.provider_metered_attempts,
            self.retries,
            self.fallback_count,
            self.input_tokens,
            self.output_tokens,
            self.cached_input_tokens,
        )
        if self.capture_complete and any(value is None for value in usage_values):
            raise ValueError("complete captures require explicit Provider usage values")
        if (
            self.cached_input_tokens is not None
            and self.input_tokens is not None
            and self.cached_input_tokens > self.input_tokens
        ):
            raise ValueError("cached input tokens cannot exceed input tokens")
        if (
            self.provider_metered_attempts is not None
            and self.provider_attempts is not None
            and self.provider_metered_attempts > self.provider_attempts
        ):
            raise ValueError("metered attempts cannot exceed attempted requests")
        if (
            self.retries is not None
            and self.provider_attempts is not None
            and self.retries
            != max(0, self.provider_attempts - (1 if self.provider_attempts else 0))
        ):
            raise ValueError("retry count does not match Provider attempts")
        if (
            self.provider_attempts is not None
            and len(self.provider_trace_id_sha256s) != self.provider_attempts
        ):
            raise ValueError("one hashed trace ID is required per Provider attempt")
        if len(self.provider_trace_id_sha256s) != len(set(self.provider_trace_id_sha256s)):
            raise ValueError("Provider trace hashes must be unique")
        if self.capture_complete and self.provider_metered_attempts != self.provider_attempts:
            raise ValueError("complete captures require every Provider attempt to be metered")
        if self.cohort.first_or_recovery == "recovery":
            if self.capture_complete and self.sse_resume_seconds is None:
                raise ValueError("recovery captures require SSE resume timing")
            if any(value != 0 for value in usage_values):
                raise ValueError("recovery captures must prove zero Provider calls")
            if any(
                value is not None
                for value in (
                    self.decision_complete_seconds,
                    self.provider_first_item_seconds,
                    self.followup_first_visible_seconds,
                    self.generation_complete_seconds,
                    self.next_question_visible_seconds,
                    self.turn_complete_seconds,
                )
            ):
                raise ValueError("recovery timing cannot be relabeled as first execution")
            return self
        if self.sse_resume_seconds is not None:
            raise ValueError("first execution cannot report SSE resume timing")
        if self.capture_complete and self.turn_complete_seconds is None:
            raise ValueError("first execution requires turn completion timing")
        if self.followup_count_before == 2 and self.provider_attempts not in {0, None}:
            raise ValueError("terminal follow-up guard must prove zero Provider calls")
        if self.capture_complete and self.cohort.followup_or_next_question == "follow_up":
            required = (
                self.provider_first_item_seconds,
                self.followup_first_visible_seconds,
                self.generation_complete_seconds,
            )
            if any(value is None for value in required):
                raise ValueError("follow-up captures require Provider and visible timing")
            if self.next_question_visible_seconds is not None:
                raise ValueError("follow-up captures cannot report next-question timing")
        elif self.capture_complete and self.next_question_visible_seconds is None:
            raise ValueError("next-question captures require visible completion timing")
        if (
            self.capture_complete
            and
            self.cohort.fixed_or_adaptive == "adaptive_v1"
            and self.followup_count_before < 2
            and self.decision_complete_seconds is None
        ):
            raise ValueError("adaptive first execution requires Decision timing")
        if self.cohort.fixed_or_adaptive == "fixed_v1" and any(
            value is not None
            for value in (self.decision_complete_seconds, self.decision_output_tokens)
        ):
            raise ValueError("fixed_v1 cannot fabricate Decision evidence")
        observed_source_names = [
            self.timing_sources[name]
            for name, value in (
                ("decision_complete", self.decision_complete_seconds),
                ("provider_first_item", self.provider_first_item_seconds),
                ("followup_first_visible", self.followup_first_visible_seconds),
                ("generation_complete", self.generation_complete_seconds),
                ("next_question_visible", self.next_question_visible_seconds),
                ("turn_complete", self.turn_complete_seconds),
            )
            if value is not None and name in self.timing_sources
        ]
        if len(observed_source_names) != len(set(observed_source_names)):
            raise ValueError("one timing source cannot be relabeled as two boundaries")
        return self


class CapturingAgentRunRecorder:
    def __init__(self) -> None:
        self.records: list[AgentRunRecord] = []

    def record(self, record: AgentRunRecord) -> None:
        sanitized = sanitize_agent_safe_metadata(record.safe_metadata)
        self.records.append(record.model_copy(update={"safe_metadata": sanitized.value}))

    def one(
        self,
        *,
        session_id: str,
        command_id: str,
        operation: str,
    ) -> AgentRunRecord:
        matches = [
            record
            for record in self.records
            if record.session_id == session_id
            and record.command_id == command_id
            and record.operation == operation
        ]
        if len(matches) != 1:
            raise ValueError(
                "expected exactly one correlated Agent run for session/command/operation"
            )
        return matches[0]


class RuntimeEvidenceBuildResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["t65-runtime-evidence-build-v1"] = (
        "t65-runtime-evidence-build-v1"
    )
    status: Literal[
        "COMPLETE",
        "BLOCKED_NOT_OBSERVABLE",
        "BLOCKED_INSUFFICIENT_EVIDENCE",
    ]
    performance_artifact: FollowupPerformanceArtifact | None = None
    metrics: dict[str, Any] | None = None
    observability: T65PerformanceObservability | None = None
    hard_stop_conditions: list[SafeIdentifier] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_result(self):
        if self.status == "COMPLETE":
            if self.performance_artifact is None or self.metrics is None:
                raise ValueError("complete runtime evidence requires artifact and metrics")
            if self.observability is not None or self.hard_stop_conditions:
                raise ValueError("complete runtime evidence cannot carry blockers")
        else:
            if self.performance_artifact is not None or self.metrics is not None:
                raise ValueError("blocked runtime evidence cannot expose a complete artifact")
            if self.observability is None or not self.hard_stop_conditions:
                raise ValueError("blocked runtime evidence requires observability and blockers")
        return self


def validate_capture_plan(plan: T65RuntimeCapturePlan, gate: GateConfig) -> None:
    expected_dimensions = [
        "cold_or_warm",
        "fixed_or_adaptive",
        "followup_or_next_question",
        "first_or_recovery",
        "schema_version",
        "question_count",
        "provider_path",
    ]
    if gate.cohort_dimensions != expected_dimensions:
        raise ValueError("GateConfig cohort dimensions drifted")
    required_samples = max(
        gate.resolve_rule("operations.adaptive_decision_p95_seconds").min_sample_size,
        gate.resolve_rule("operations.adaptive_followup_e2e_ttft_p95_seconds").min_sample_size,
        gate.resolve_rule("operations.adaptive_next_question_p95_seconds").min_sample_size,
        gate.resolve_rule("operations.sse_resume_p95_seconds").min_sample_size,
    )
    for cohort in plan.cohorts:
        if cohort.target_samples < required_samples:
            raise ValueError(
                f"cohort {cohort.identity()} requires at least {required_samples} samples"
            )
    identities = {cohort.identity() for cohort in plan.cohorts}
    for cohort in plan.cohorts:
        if (
            cohort.fixed_or_adaptive == "adaptive_v1"
            and cohort.followup_or_next_question == "follow_up"
            and cohort.first_or_recovery == "first"
        ):
            baseline = list(cohort.identity())
            baseline[1] = "fixed_v1"
            if tuple(baseline) not in identities:
                raise ValueError("adaptive follow-up cohort lacks comparable fixed_v1 baseline")


def correlate_runtime_boundaries(
    *,
    sample_id: str,
    session_id: str,
    question_id: str,
    command_id: str,
    cohort: RuntimeCohortSpec,
    external_stopwatch: Mapping[str, float],
    decision_duration_ms: float | None,
    agent_record: AgentRunRecord | None,
    sse_measurement: SseRecoveryMeasurement | None,
    provider_trace_id_sha256s: list[str],
    usage: Mapping[str, int],
    followup_count_before: int = 0,
    decision_degraded: bool = False,
) -> CapturedTimingBoundaries:
    if cohort.first_or_recovery == "recovery":
        if sse_measurement is None:
            raise ValueError("recovery correlation requires an SSE measurement")
        if any(key not in usage for key in _USAGE_KEYS) or any(
            int(usage[key]) != 0 for key in _USAGE_KEYS
        ):
            raise ValueError("recovery correlation must prove zero Provider usage")
        return CapturedTimingBoundaries(
            sample_id=sample_id,
            session_id=session_id,
            question_id=question_id,
            command_id=command_id,
            cohort=cohort,
            followup_count_before=followup_count_before,
            sse_resume_seconds=sse_measurement.resume_seconds,
            provider_attempts=0,
            provider_metered_attempts=0,
            retries=0,
            input_tokens=0,
            output_tokens=0,
            cached_input_tokens=0,
            provider_trace_id_sha256s=[],
            timing_sources={"sse_resume": "cursor_resume_stopwatch"},
        )
    first_item = None
    if agent_record is not None:
        if (
            agent_record.session_id != session_id
            or agent_record.command_id != command_id
        ):
            raise ValueError("Agent run does not match session and command correlation")
        value = agent_record.safe_metadata.get("first_item_latency_ms")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            first_item = float(value) / 1000.0
    attempted = _optional_int(usage.get("provider_attempt_count"))
    metered = _optional_int(usage.get("provider_metered_attempt_count"))
    input_tokens = _optional_int(usage.get("provider_input_tokens"))
    output_tokens = _optional_int(usage.get("provider_output_tokens"))
    cached_input_tokens = _optional_int(usage.get("provider_cached_input_tokens"))
    usage_complete = all(
        value is not None
        for value in (
            attempted,
            metered,
            input_tokens,
            output_tokens,
            cached_input_tokens,
        )
    )
    signals_complete = (
        external_stopwatch.get("turn_complete_seconds") is not None
        and (
            cohort.first_or_recovery != "first"
            or cohort.followup_or_next_question != "follow_up"
            or (
                first_item is not None
                and external_stopwatch.get("followup_first_visible_seconds") is not None
                and external_stopwatch.get("generation_complete_seconds") is not None
            )
        )
        and (
            cohort.followup_or_next_question != "next_question"
            or external_stopwatch.get("next_question_visible_seconds") is not None
        )
        and (
            cohort.fixed_or_adaptive != "adaptive_v1"
            or followup_count_before == 2
            or decision_duration_ms is not None
        )
    )
    return CapturedTimingBoundaries(
        sample_id=sample_id,
        session_id=session_id,
        question_id=question_id,
        command_id=command_id,
        cohort=cohort,
        capture_complete=usage_complete and signals_complete,
        followup_count_before=followup_count_before,
        decision_complete_seconds=(
            None if decision_duration_ms is None else float(decision_duration_ms) / 1000.0
        ),
        provider_first_item_seconds=first_item,
        followup_first_visible_seconds=external_stopwatch.get("followup_first_visible_seconds"),
        generation_complete_seconds=external_stopwatch.get("generation_complete_seconds"),
        next_question_visible_seconds=external_stopwatch.get("next_question_visible_seconds"),
        turn_complete_seconds=external_stopwatch.get("turn_complete_seconds"),
        provider_attempts=attempted,
        provider_metered_attempts=metered,
        retries=(
            max(0, attempted - (1 if attempted else 0))
            if attempted is not None
            else None
        ),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_input_tokens=cached_input_tokens,
        decision_output_tokens=_optional_int(usage.get("decision_output_tokens")),
        followup_output_tokens=_optional_int(usage.get("followup_output_tokens")),
        decision_degraded=decision_degraded,
        provider_trace_id_sha256s=provider_trace_id_sha256s,
        timing_sources={
            "decision_complete": "decision_service_duration",
            "provider_first_item": "agent_stream_first_item",
            "followup_first_visible": "external_sse_stopwatch",
            "generation_complete": "generation_completion_stopwatch",
            "next_question_visible": "external_next_question_stopwatch",
            "turn_complete": "external_turn_stopwatch",
        },
    )


def build_runtime_performance_evidence(
    *,
    plan: T65RuntimeCapturePlan,
    captures: Sequence[CapturedTimingBoundaries],
    pricing: PerformancePricingSnapshot,
    source_capture_sha256: str,
    provider_name: str,
    model_id: str,
    capture_run_id: str,
    gate_config: GateConfig,
) -> RuntimeEvidenceBuildResult:
    validate_capture_plan(plan, gate_config)
    counts = Counter(capture.cohort.identity() for capture in captures)
    missing_samples = [
        cohort
        for cohort in plan.cohorts
        if counts[cohort.identity()] < cohort.target_samples
    ]
    missing_signals = _missing_required_signals(captures)
    usage_incomplete = any(
        capture.provider_attempts is None
        or capture.provider_metered_attempts is None
        or capture.retries is None
        or capture.input_tokens is None
        or capture.output_tokens is None
        or capture.cached_input_tokens is None
        or capture.provider_attempts != capture.provider_metered_attempts
        for capture in captures
    )
    if missing_samples or missing_signals or usage_incomplete:
        if usage_incomplete:
            blocker = "USAGE_METERING_UNAVAILABLE"
            status = "BLOCKED_NOT_OBSERVABLE"
            quality_status = "BLOCKED_PERFORMANCE_SIGNAL_NOT_OBSERVABLE"
        elif missing_signals:
            blocker = "PERFORMANCE_SIGNAL_NOT_OBSERVABLE"
            status = "BLOCKED_NOT_OBSERVABLE"
            quality_status = "BLOCKED_PERFORMANCE_SIGNAL_NOT_OBSERVABLE"
        else:
            blocker = "INSUFFICIENT_SAMPLE"
            status = "BLOCKED_INSUFFICIENT_EVIDENCE"
            quality_status = "BLOCKED_INSUFFICIENT_SAMPLE"
        observability = T65PerformanceObservability(
            candidate_revision=plan.candidate_revision,
            candidate_tree=plan.candidate_tree,
            provider=provider_name,
            model=model_id,
            source_artifact_sha256s=[source_capture_sha256],
            signals=_observability_signals(captures, missing_signals, missing_samples),
            usage_ledger_sha256=None,
            quality_status=quality_status,
            hard_stop_conditions=[blocker],
        )
        return RuntimeEvidenceBuildResult(
            status=status,
            observability=observability,
            hard_stop_conditions=[blocker],
        )

    samples = [
        _to_performance_sample(
            capture,
            pricing=pricing,
            provider_name=provider_name,
            model_id=model_id,
        )
        for capture in captures
    ]
    artifact = FollowupPerformanceArtifact(
        source_kind="live_provider",
        provider_name=provider_name,
        model_id=model_id,
        capture_run_id=capture_run_id,
        source_capture_sha256=source_capture_sha256,
        pricing_snapshot=pricing,
        samples=samples,
    )
    metrics = evaluate_followup_performance(artifact, gate_config=gate_config)
    return RuntimeEvidenceBuildResult(
        status="COMPLETE",
        performance_artifact=artifact,
        metrics=metrics,
    )


_USAGE_KEYS = (
    "provider_attempt_count",
    "provider_metered_attempt_count",
    "provider_input_tokens",
    "provider_output_tokens",
    "provider_cached_input_tokens",
)


def _optional_int(value: Any) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


def _missing_required_signals(
    captures: Sequence[CapturedTimingBoundaries],
) -> set[str]:
    missing: set[str] = set()
    for capture in captures:
        if not capture.capture_complete:
            if capture.cohort.first_or_recovery == "recovery":
                missing.add("sse_resume")
            elif capture.cohort.followup_or_next_question == "follow_up":
                missing.update(
                    {
                        "provider_first_item",
                        "followup_first_visible",
                        "generation_complete",
                    }
                )
            else:
                missing.add("next_question_visible")
            continue
        if capture.cohort.first_or_recovery == "recovery":
            if capture.sse_resume_seconds is None:
                missing.add("sse_resume")
            continue
        if capture.turn_complete_seconds is None:
            missing.add("turn_complete")
        if capture.cohort.followup_or_next_question == "follow_up":
            if capture.provider_first_item_seconds is None:
                missing.add("provider_first_item")
            if capture.followup_first_visible_seconds is None:
                missing.add("followup_first_visible")
            if capture.generation_complete_seconds is None:
                missing.add("generation_complete")
        elif capture.next_question_visible_seconds is None:
            missing.add("next_question_visible")
        if (
            capture.cohort.fixed_or_adaptive == "adaptive_v1"
            and capture.followup_count_before < 2
            and capture.decision_complete_seconds is None
        ):
            missing.add("decision_complete")
    return missing


def _observability_signals(
    captures: Sequence[CapturedTimingBoundaries],
    missing_signals: set[str],
    missing_samples: Sequence[RuntimeCohortSpec],
) -> list[PerformanceSignal]:
    values: dict[str, list[float]] = {
        "decision_complete": [],
        "provider_first_item": [],
        "followup_first_visible": [],
        "generation_complete": [],
        "next_question_visible": [],
        "sse_resume": [],
        "report_complete": [],
    }
    fields = {
        "decision_complete": "decision_complete_seconds",
        "provider_first_item": "provider_first_item_seconds",
        "followup_first_visible": "followup_first_visible_seconds",
        "generation_complete": "generation_complete_seconds",
        "next_question_visible": "next_question_visible_seconds",
        "sse_resume": "sse_resume_seconds",
    }
    for capture in captures:
        for name, field in fields.items():
            value = getattr(capture, field)
            if value is not None:
                values[name].append(value)
    sample_blocked = bool(missing_samples)
    result: list[PerformanceSignal] = []
    for name, observed in values.items():
        if name in missing_signals:
            status = "not_observable"
            reason = "required runtime boundary was not observed"
        elif sample_blocked or not observed:
            status = "insufficient_sample"
            reason = "frozen cohort has fewer than the required samples"
        else:
            status = "observed"
            reason = None
        result.append(
            PerformanceSignal(
                name=name,
                status=status,
                seconds=max(observed) if status == "observed" else None,
                sample_count=len(observed),
                source_artifact_sha256=None,
                reason=reason,
            )
        )
    return result


def _to_performance_sample(
    capture: CapturedTimingBoundaries,
    *,
    pricing: PerformancePricingSnapshot,
    provider_name: str,
    model_id: str,
) -> FollowupPerformanceSample:
    if any(
        value is None
        for value in (
            capture.provider_attempts,
            capture.provider_metered_attempts,
            capture.retries,
            capture.fallback_count,
            capture.input_tokens,
            capture.output_tokens,
            capture.cached_input_tokens,
        )
    ):
        raise ValueError("incomplete Provider usage cannot become a performance sample")
    assert capture.provider_attempts is not None
    assert capture.retries is not None
    assert capture.fallback_count is not None
    assert capture.input_tokens is not None
    assert capture.output_tokens is not None
    assert capture.cached_input_tokens is not None
    estimated_cost = (
        pricing.estimate(
            input_tokens=capture.input_tokens,
            output_tokens=capture.output_tokens,
            cached_input_tokens=capture.cached_input_tokens,
        )
        if capture.provider_attempts
        else 0.0
    )
    return FollowupPerformanceSample(
        sample_id=capture.sample_id,
        session_id=capture.session_id,
        question_id=capture.question_id,
        policy_version=capture.cohort.fixed_or_adaptive,
        cold_or_warm=capture.cohort.cold_or_warm,
        followup_or_next_question=capture.cohort.followup_or_next_question,
        first_or_recovery=capture.cohort.first_or_recovery,
        schema_version=capture.cohort.schema_version,
        question_count=capture.cohort.question_count,
        provider_path=capture.cohort.provider_path,
        source_kind="live_provider",
        provider_name=provider_name,
        model_id=model_id,
        provider_request_trace_ids=list(capture.provider_trace_id_sha256s),
        followup_count_before=capture.followup_count_before,
        decision_latency_seconds=capture.decision_complete_seconds,
        generation_ttft_seconds=capture.provider_first_item_seconds,
        generation_complete_seconds=capture.generation_complete_seconds,
        followup_e2e_ttft_seconds=capture.followup_first_visible_seconds,
        next_question_e2e_seconds=capture.next_question_visible_seconds,
        turn_latency_seconds=capture.turn_complete_seconds,
        sse_resume_seconds=capture.sse_resume_seconds,
        input_tokens=capture.input_tokens,
        output_tokens=capture.output_tokens,
        cached_input_tokens=capture.cached_input_tokens,
        decision_output_tokens=capture.decision_output_tokens,
        followup_output_tokens=capture.followup_output_tokens,
        planned_provider_requests=(
            0 if capture.cohort.first_or_recovery == "recovery" else min(1, capture.provider_attempts)
        ),
        actual_provider_requests=capture.provider_attempts,
        retry_count=capture.retries,
        fallback_count=capture.fallback_count,
        provider_calls_per_answer=capture.provider_attempts,
        provider_calls_per_main_question=capture.provider_attempts,
        decision_degraded=capture.decision_degraded,
        estimated_cost=estimated_cost,
    )
