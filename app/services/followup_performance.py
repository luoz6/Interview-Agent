from __future__ import annotations

import math
from collections import defaultdict
from time import perf_counter
from typing import Annotated, Any, Iterable, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.services.interview_quality_gate import (
    GateConfig,
    MetricEvaluation,
    evaluate_metric,
)


PerformanceSource = Literal[
    "synthetic_fixture",
    "saved_provider_replay",
    "live_provider",
]
PolicyVersion = Literal["fixed_v1", "adaptive_v1"]
StartupClass = Literal["cold", "warm"]
ExecutionClass = Literal["first", "recovery"]
OutcomePath = Literal["follow_up", "next_question"]
SafeIdentifier = Annotated[
    str,
    Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9_.:@+\-]+$",
    ),
]


class FollowupPerformanceSample(BaseModel):
    """One privacy-safe performance observation.

    First executions contain Provider and end-to-end timing. Recovery executions
    contain only replay/resume timing and cannot silently duplicate Provider usage.
    The model intentionally forbids prompts, answers, generated text, or raw response
    fields so a performance artifact remains safe to publish.
    """

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    sample_id: SafeIdentifier
    session_id: SafeIdentifier
    question_id: SafeIdentifier
    policy_version: PolicyVersion
    cold_or_warm: StartupClass
    followup_or_next_question: OutcomePath
    first_or_recovery: ExecutionClass
    schema_version: SafeIdentifier
    question_count: int = Field(ge=1)
    provider_path: SafeIdentifier
    source_kind: PerformanceSource
    provider_name: SafeIdentifier | None = None
    model_id: SafeIdentifier | None = None
    capture_complete: bool = True
    provider_request_trace_ids: list[SafeIdentifier] = Field(default_factory=list)

    followup_count_before: int = Field(default=0, ge=0, le=2)
    decision_latency_seconds: float | None = Field(default=None, ge=0)
    generation_ttft_seconds: float | None = Field(default=None, ge=0)
    generation_complete_seconds: float | None = Field(default=None, ge=0)
    followup_e2e_ttft_seconds: float | None = Field(default=None, ge=0)
    next_question_e2e_seconds: float | None = Field(default=None, ge=0)
    turn_latency_seconds: float | None = Field(default=None, ge=0)
    sse_resume_seconds: float | None = Field(default=None, ge=0)

    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    cached_input_tokens: int = Field(default=0, ge=0)
    decision_output_tokens: int | None = Field(default=None, ge=0)
    followup_output_tokens: int | None = Field(default=None, ge=0)
    planned_provider_requests: int = Field(default=0, ge=0)
    actual_provider_requests: int = Field(default=0, ge=0)
    retry_count: int = Field(default=0, ge=0)
    fallback_count: int = Field(default=0, ge=0)
    provider_calls_per_answer: int = Field(default=0, ge=0)
    provider_calls_per_main_question: int = Field(default=0, ge=0)
    decision_degraded: bool = False
    estimated_cost: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_performance_contract(self):
        if self.cached_input_tokens > self.input_tokens:
            raise ValueError("cached_input_tokens cannot exceed input_tokens")
        if self.actual_provider_requests != (
            self.planned_provider_requests + self.retry_count
        ):
            raise ValueError(
                "actual_provider_requests must equal planned requests plus retries"
            )
        if self.provider_calls_per_answer != self.actual_provider_requests:
            raise ValueError(
                "provider_calls_per_answer must equal actual requests for the answer"
            )
        if self.provider_calls_per_main_question < self.provider_calls_per_answer:
            raise ValueError(
                "main-question calls cannot be lower than calls for this answer"
            )
        if self.fallback_count > self.actual_provider_requests:
            raise ValueError("fallback_count cannot exceed actual Provider requests")
        stage_output = sum(
            value or 0
            for value in (self.decision_output_tokens, self.followup_output_tokens)
        )
        if stage_output > self.output_tokens:
            raise ValueError("stage output tokens cannot exceed total output tokens")
        if self.actual_provider_requests == 0 and any(
            value != 0
            for value in (
                self.input_tokens,
                self.output_tokens,
                self.cached_input_tokens,
                self.retry_count,
                self.fallback_count,
            )
        ):
            raise ValueError("zero-call samples cannot report Provider usage")
        if self.policy_version == "fixed_v1":
            if self.decision_latency_seconds is not None:
                raise ValueError("fixed_v1 has no Decision latency")
            if self.decision_output_tokens is not None:
                raise ValueError("fixed_v1 has no Decision output tokens")
        if self.followup_count_before == 2:
            if self.followup_or_next_question != "next_question":
                raise ValueError("the second follow-up guard must choose next_question")
            if self.actual_provider_requests != 0:
                raise ValueError("the second follow-up guard must use zero Provider calls")
            if self.decision_latency_seconds is not None:
                raise ValueError("the terminal guard cannot fabricate Decision latency")
            if self.decision_output_tokens is not None or self.decision_degraded:
                raise ValueError("the terminal guard cannot fabricate Decision output")

        if self.first_or_recovery == "recovery":
            if self.sse_resume_seconds is None:
                raise ValueError("recovery samples require sse_resume_seconds")
            if self.actual_provider_requests != 0 or any(
                value != 0
                for value in (
                    self.input_tokens,
                    self.output_tokens,
                    self.cached_input_tokens,
                    self.retry_count,
                    self.fallback_count,
                )
            ):
                raise ValueError("SSE recovery cannot duplicate Provider usage")
            forbidden_timings = (
                self.decision_latency_seconds,
                self.generation_ttft_seconds,
                self.generation_complete_seconds,
                self.followup_e2e_ttft_seconds,
                self.next_question_e2e_seconds,
                self.turn_latency_seconds,
            )
            if any(value is not None for value in forbidden_timings):
                raise ValueError("recovery timing must remain separate from first execution")
            return self

        if self.sse_resume_seconds is not None:
            raise ValueError("first executions cannot report SSE resume latency")
        if (
            self.policy_version == "adaptive_v1"
            and self.followup_count_before < 2
            and self.decision_latency_seconds is None
        ):
            raise ValueError("adaptive first execution requires Decision latency")
        if self.turn_latency_seconds is None:
            raise ValueError("first executions require total turn latency")

        if self.followup_or_next_question == "follow_up":
            if any(
                value is None
                for value in (
                    self.generation_ttft_seconds,
                    self.generation_complete_seconds,
                    self.followup_e2e_ttft_seconds,
                )
            ):
                raise ValueError(
                    "follow-up execution requires Generation TTFT, complete, and E2E TTFT"
                )
            if self.next_question_e2e_seconds is not None:
                raise ValueError("follow-up execution cannot report next-question latency")
            assert self.generation_ttft_seconds is not None
            assert self.generation_complete_seconds is not None
            assert self.followup_e2e_ttft_seconds is not None
            if self.generation_complete_seconds < self.generation_ttft_seconds:
                raise ValueError("Generation complete latency cannot precede TTFT")
            minimum_e2e = self.generation_ttft_seconds + (
                self.decision_latency_seconds or 0
            )
            if self.followup_e2e_ttft_seconds < minimum_e2e:
                raise ValueError("follow-up E2E TTFT cannot omit Decision or Generation TTFT")
            minimum_turn = self.generation_complete_seconds + (
                self.decision_latency_seconds or 0
            )
            if self.turn_latency_seconds < minimum_turn:
                raise ValueError("turn latency cannot omit Decision or Generation completion")
        else:
            if self.next_question_e2e_seconds is None:
                raise ValueError("next-question execution requires next-question E2E latency")
            if any(
                value is not None
                for value in (
                    self.generation_ttft_seconds,
                    self.generation_complete_seconds,
                    self.followup_e2e_ttft_seconds,
                    self.followup_output_tokens,
                )
            ):
                raise ValueError("next-question execution cannot report Generation metrics")
            if self.turn_latency_seconds < self.next_question_e2e_seconds:
                raise ValueError("turn latency cannot precede next-question completion")
        return self


class PerformancePricingSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    source_url: Literal["https://api-docs.deepseek.com/quick_start/pricing"]
    observed_at: str = Field(
        pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$"
    )
    currency: Literal["USD"] = "USD"
    cache_hit_input_per_million: float = Field(ge=0)
    cache_miss_input_per_million: float = Field(ge=0)
    output_per_million: float = Field(ge=0)

    def estimate(
        self,
        *,
        input_tokens: int,
        output_tokens: int,
        cached_input_tokens: int,
    ) -> float:
        cached = min(input_tokens, cached_input_tokens)
        uncached = input_tokens - cached
        return (
            cached / 1_000_000 * self.cache_hit_input_per_million
            + uncached / 1_000_000 * self.cache_miss_input_per_million
            + output_tokens / 1_000_000 * self.output_per_million
        )


class FollowupPerformanceArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["followup-performance-replay-v1"] = (
        "followup-performance-replay-v1"
    )
    source_kind: PerformanceSource
    capture_status: Literal["complete", "hard_stopped"] = "complete"
    provider_name: SafeIdentifier | None = None
    model_id: SafeIdentifier | None = None
    capture_run_id: SafeIdentifier | None = None
    source_capture_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    pricing_snapshot: PerformancePricingSnapshot | None = None
    hard_stop_conditions: list[SafeIdentifier] = Field(default_factory=list)
    samples: list[FollowupPerformanceSample] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_artifact(self):
        identifiers = [sample.sample_id for sample in self.samples]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("performance sample IDs must be unique")
        if any(sample.source_kind != self.source_kind for sample in self.samples):
            raise ValueError("all samples must match the artifact source kind")
        if self.capture_status == "hard_stopped" and not self.hard_stop_conditions:
            raise ValueError("hard-stopped artifacts require stop conditions")
        if self.capture_status == "complete" and self.hard_stop_conditions:
            raise ValueError("complete artifacts cannot carry hard-stop conditions")
        if self.source_kind != "synthetic_fixture":
            if not all(
                (
                    self.provider_name,
                    self.model_id,
                    self.capture_run_id,
                    self.source_capture_sha256,
                )
            ):
                raise ValueError(
                    "real Provider artifacts require Provider/model and source-capture provenance"
                )
            if self.capture_status == "complete" and self.pricing_snapshot is None:
                raise ValueError("complete real artifacts require a pricing snapshot")
            trace_ids: list[str] = []
            for sample in self.samples:
                if (
                    sample.provider_name != self.provider_name
                    or sample.model_id != self.model_id
                ):
                    raise ValueError("sample Provider/model does not match the artifact")
                if self.capture_status == "complete" and not sample.capture_complete:
                    raise ValueError("complete real artifacts require complete samples")
                if len(sample.provider_request_trace_ids) != sample.actual_provider_requests:
                    raise ValueError(
                        "real samples require one trace ID per Provider request"
                    )
                trace_ids.extend(sample.provider_request_trace_ids)
                if (
                    self.capture_status == "complete"
                    and sample.first_or_recovery == "first"
                    and sample.actual_provider_requests > 0
                    and (
                        sample.input_tokens <= 0
                        or sample.output_tokens <= 0
                        or sample.estimated_cost is None
                    )
                ):
                    raise ValueError(
                        "complete real Provider executions require token and cost metering"
                    )
                if (
                    self.capture_status == "complete"
                    and sample.first_or_recovery == "first"
                    and sample.estimated_cost is not None
                    and self.pricing_snapshot is not None
                ):
                    expected = self.pricing_snapshot.estimate(
                        input_tokens=sample.input_tokens,
                        output_tokens=sample.output_tokens,
                        cached_input_tokens=sample.cached_input_tokens,
                    )
                    if not math.isclose(
                        sample.estimated_cost,
                        expected,
                        rel_tol=1e-9,
                        abs_tol=1e-12,
                    ):
                        raise ValueError(
                            "estimated cost does not match the frozen pricing snapshot"
                        )
            if len(trace_ids) != len(set(trace_ids)):
                raise ValueError("Provider request trace IDs must be unique")
        return self


class SseRecoveryMeasurement(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    disconnected_after_event_count: int = Field(ge=1)
    resumed_event_count: int = Field(ge=1)
    resume_seconds: float = Field(ge=0)
    last_event_id_before_disconnect: SafeIdentifier
    first_resumed_event_id: SafeIdentifier
    duplicate_event_count: Literal[0] = 0


def nearest_rank(values: Iterable[float], fraction: float) -> float:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        raise ValueError("at least one value is required")
    if not 0 < fraction <= 1:
        raise ValueError("fraction must be in (0, 1]")
    return ordered[max(0, math.ceil(len(ordered) * fraction) - 1)]


def evaluate_followup_performance(
    artifact: FollowupPerformanceArtifact,
    *,
    gate_config: GateConfig,
) -> dict[str, Any]:
    expected_dimensions = [
        "cold_or_warm",
        "fixed_or_adaptive",
        "followup_or_next_question",
        "first_or_recovery",
        "schema_version",
        "question_count",
        "provider_path",
    ]
    if gate_config.cohort_dimensions != expected_dimensions:
        raise ValueError(
            "GateConfig cohort dimensions drifted from the T37 evaluator contract"
        )
    samples = artifact.samples
    gate_results: list[dict[str, Any]] = []

    def add_gate(result: MetricEvaluation, cohort: dict[str, Any] | None = None):
        gate_results.append(
            {
                **result.model_dump(mode="json"),
                "cohort": cohort or {},
            }
        )

    _add_scalar_gates(samples, gate_config, add_gate)
    _add_latency_gates(samples, gate_config, add_gate)

    cohort_summaries = _cohort_summaries(samples)
    comparisons = _fixed_adaptive_comparisons(samples)
    session_usage = _session_usage(samples)
    statuses = {item["status"] for item in gate_results if item["blocking"]}
    if "FAIL" in statuses:
        automated_status = "FAIL"
    elif statuses & {"INSUFFICIENT_SAMPLE", "INSUFFICIENT_BASELINE"}:
        automated_status = "BLOCKED"
    else:
        automated_status = "PASS"

    real_complete = (
        artifact.source_kind in {"saved_provider_replay", "live_provider"}
        and artifact.capture_status == "complete"
        and all(sample.capture_complete for sample in samples)
    )
    if not real_complete:
        quality_status = (
            "BLOCKED_INCOMPLETE_PROVIDER_CAPTURE"
            if artifact.capture_status == "hard_stopped"
            else "BLOCKED_NOT_RUN_REAL_PROVIDER"
        )
    elif automated_status == "PASS":
        quality_status = "PASS"
    elif automated_status == "FAIL":
        quality_status = "FAIL"
    else:
        quality_status = "BLOCKED_INSUFFICIENT_EVIDENCE"

    engineering_status = "PASS" if automated_status == "PASS" else automated_status
    overall_status = (
        "PASS"
        if engineering_status == "PASS" and quality_status == "PASS"
        else "FAIL"
        if engineering_status == "FAIL" or quality_status == "FAIL"
        else "BLOCKED"
    )
    return {
        "schema_version": "followup-performance-metrics-v1",
        "sample_count": len(samples),
        "source_kind": artifact.source_kind,
        "capture_status": artifact.capture_status,
        "engineering_status": engineering_status,
        "quality_status": quality_status,
        "overall_status": overall_status,
        "automated_gate_status": automated_status,
        "gate_results": gate_results,
        "cohort_summaries": cohort_summaries,
        "fixed_adaptive_same_path_comparisons": comparisons,
        "session_usage": session_usage,
        "anomaly_cases": _anomaly_cases(samples, gate_results),
        "hard_stop_conditions": list(artifact.hard_stop_conditions),
        "fixed_decision_latency_baseline": None,
        "fixed_decision_latency_baseline_reason": (
            "fixed_v1 has no Decision stage; a zero baseline is prohibited"
        ),
    }


def measure_sse_resume(
    event_stream_service,
    *,
    session_id: str,
    command_id: str,
    disconnect_after_events: int = 1,
    clock=perf_counter,
) -> SseRecoveryMeasurement:
    """Interrupt and resume the real cursor-based event iterator without payloads.

    Only stable event IDs and timing leave this function. Chunk text is never copied
    into the measurement, and duplicate replay is treated as a correctness failure.
    """

    if disconnect_after_events < 1:
        raise ValueError("disconnect_after_events must be positive")
    initial_iterator = iter(
        event_stream_service.iter_command_events(session_id, command_id)
    )
    initial_ids: list[str] = []
    try:
        for _ in range(disconnect_after_events):
            initial_ids.append(_event_id(next(initial_iterator)))
    except StopIteration as exc:
        raise ValueError("stream ended before the simulated disconnect") from exc
    finally:
        close = getattr(initial_iterator, "close", None)
        if callable(close):
            close()

    started = clock()
    resumed_iterator = iter(
        event_stream_service.iter_command_events(
            session_id,
            command_id,
            after_event_id=initial_ids[-1],
        )
    )
    try:
        first = next(resumed_iterator)
    except StopIteration as exc:
        raise ValueError("resumed stream did not produce a new event") from exc
    resume_seconds = max(0.0, float(clock()) - float(started))
    resumed_ids = [_event_id(first), *[_event_id(item) for item in resumed_iterator]]
    duplicates = set(initial_ids) & set(resumed_ids)
    if duplicates:
        raise ValueError("SSE resume replayed an event already shown to the user")
    return SseRecoveryMeasurement(
        disconnected_after_event_count=len(initial_ids),
        resumed_event_count=len(resumed_ids),
        resume_seconds=resume_seconds,
        last_event_id_before_disconnect=initial_ids[-1],
        first_resumed_event_id=resumed_ids[0],
        duplicate_event_count=0,
    )


def _event_id(event) -> str:
    return (
        f"{event.generation_id}:{event.attempt_number}:"
        f"{getattr(event, 'sequence', 0)}"
    )


def _add_scalar_gates(samples, gate_config, add_gate) -> None:
    first = [sample for sample in samples if sample.first_or_recovery == "first"]
    decision_tokens = [
        sample.decision_output_tokens
        for sample in first
        if sample.decision_output_tokens is not None
    ]
    followup_tokens = [
        sample.followup_output_tokens
        for sample in first
        if sample.followup_output_tokens is not None
    ]
    after_second = [sample for sample in first if sample.followup_count_before == 2]
    scalar_specs = (
        ("operations.decision_output_tokens", decision_tokens, max),
        ("operations.followup_output_tokens", followup_tokens, max),
        (
            "operations.provider_calls_per_answer",
            [sample.provider_calls_per_answer for sample in samples],
            max,
        ),
        (
            "operations.provider_calls_per_main_question",
            [sample.provider_calls_per_main_question for sample in samples],
            max,
        ),
        (
            "operations.provider_calls_after_second_followup",
            [sample.actual_provider_requests for sample in after_second],
            max,
        ),
    )
    for key, values, aggregate in scalar_specs:
        actual = float(aggregate(values)) if values else 0.0
        add_gate(
            evaluate_metric(
                gate_config,
                key,
                actual=actual,
                sample_size=len(values),
            )
        )

    planned = sum(sample.planned_provider_requests for sample in first)
    actual_requests = sum(sample.actual_provider_requests for sample in first)
    amplification = actual_requests / planned if planned else 0.0
    add_gate(
        evaluate_metric(
            gate_config,
            "operations.retry_amplification",
            actual=amplification,
            sample_size=len(first),
        )
    )
    decisions = [
        sample
        for sample in first
        if sample.policy_version == "adaptive_v1"
        and sample.decision_latency_seconds is not None
    ]
    degradation_rate = (
        sum(sample.decision_degraded for sample in decisions) / len(decisions)
        if decisions
        else 0.0
    )
    add_gate(
        evaluate_metric(
            gate_config,
            "operations.decision_degradation_rate",
            actual=degradation_rate,
            sample_size=len(decisions),
        )
    )

    sessions = _session_usage(samples)
    for metric, field in (
        ("operations.session_input_tokens", "input_tokens"),
        ("operations.session_output_tokens", "output_tokens"),
        ("operations.session_estimated_cost", "estimated_cost"),
    ):
        add_gate(
            evaluate_metric(
                gate_config,
                metric,
                actual=sum(float(item[field]) for item in sessions),
                sample_size=len(sessions),
            )
        )


def _add_latency_gates(samples, gate_config, add_gate) -> None:
    specs = (
        (
            "operations.adaptive_decision_p95_seconds",
            lambda item: item.first_or_recovery == "first"
            and item.policy_version == "adaptive_v1"
            and item.decision_latency_seconds is not None,
            "decision_latency_seconds",
            False,
        ),
        (
            "operations.adaptive_followup_e2e_ttft_p95_seconds",
            lambda item: item.first_or_recovery == "first"
            and item.policy_version == "adaptive_v1"
            and item.followup_or_next_question == "follow_up",
            "followup_e2e_ttft_seconds",
            True,
        ),
        (
            "operations.adaptive_next_question_p95_seconds",
            lambda item: item.first_or_recovery == "first"
            and item.policy_version == "adaptive_v1"
            and item.followup_or_next_question == "next_question",
            "next_question_e2e_seconds",
            False,
        ),
        (
            "operations.sse_resume_p95_seconds",
            lambda item: item.first_or_recovery == "recovery",
            "sse_resume_seconds",
            False,
        ),
    )
    for metric, predicate, field, requires_baseline in specs:
        matching = [sample for sample in samples if predicate(sample)]
        groups = _group_by_cohort(matching)
        if not groups:
            add_gate(
                evaluate_metric(
                    gate_config,
                    metric,
                    actual=0,
                    sample_size=0,
                )
            )
            continue
        for _, cohort_samples in groups:
            values = [
                getattr(sample, field)
                for sample in cohort_samples
                if getattr(sample, field) is not None
            ]
            actual = nearest_rank(values, 0.95) if values else 0.0
            cohort = _cohort_dict(cohort_samples[0])
            baseline = None
            if requires_baseline:
                baseline_samples = _matching_fixed_baseline(samples, cohort_samples[0])
                rule = gate_config.resolve_rule(metric)
                baseline_values = [
                    item.followup_e2e_ttft_seconds
                    for item in baseline_samples
                    if item.followup_e2e_ttft_seconds is not None
                ]
                if len(baseline_values) < rule.min_sample_size:
                    add_gate(
                        _insufficient_baseline(
                            gate_config,
                            metric,
                            actual=actual,
                            sample_size=len(values),
                            reason=(
                                "matching fixed_v1 cohort has "
                                f"{len(baseline_values)} samples; "
                                f"{rule.min_sample_size} required"
                            ),
                        ),
                        cohort,
                    )
                    continue
                baseline = nearest_rank(baseline_values, 0.95)
            add_gate(
                evaluate_metric(
                    gate_config,
                    metric,
                    actual=actual,
                    sample_size=len(values),
                    baseline=baseline,
                ),
                cohort,
            )


def _insufficient_baseline(
    gate_config: GateConfig,
    metric: str,
    *,
    actual: float,
    sample_size: int,
    reason: str,
) -> MetricEvaluation:
    base = evaluate_metric(
        gate_config,
        metric,
        actual=actual,
        sample_size=sample_size,
    )
    return base.model_copy(
        update={
            "status": "INSUFFICIENT_BASELINE",
            "baseline": None,
            "reason": reason,
        }
    )


def _cohort_tuple(sample: FollowupPerformanceSample) -> tuple[Any, ...]:
    return (
        sample.cold_or_warm,
        sample.policy_version,
        sample.followup_or_next_question,
        sample.first_or_recovery,
        sample.schema_version,
        sample.question_count,
        sample.provider_path,
    )


def _cohort_dict(sample: FollowupPerformanceSample) -> dict[str, Any]:
    return {
        "cold_or_warm": sample.cold_or_warm,
        "fixed_or_adaptive": sample.policy_version,
        "followup_or_next_question": sample.followup_or_next_question,
        "first_or_recovery": sample.first_or_recovery,
        "schema_version": sample.schema_version,
        "question_count": sample.question_count,
        "provider_path": sample.provider_path,
    }


def _group_by_cohort(samples):
    groups: dict[tuple[Any, ...], list[FollowupPerformanceSample]] = defaultdict(list)
    for sample in samples:
        groups[_cohort_tuple(sample)].append(sample)
    return sorted(groups.items(), key=lambda item: repr(item[0]))


def _matching_fixed_baseline(samples, adaptive_sample):
    target = list(_cohort_tuple(adaptive_sample))
    target[1] = "fixed_v1"
    return [sample for sample in samples if _cohort_tuple(sample) == tuple(target)]


def _stats(values: list[float]) -> dict[str, float] | None:
    if not values:
        return None
    return {
        "p50": nearest_rank(values, 0.50),
        "p95": nearest_rank(values, 0.95),
        "max": max(values),
    }


def _cohort_summaries(samples) -> list[dict[str, Any]]:
    fields = (
        "decision_latency_seconds",
        "generation_ttft_seconds",
        "generation_complete_seconds",
        "followup_e2e_ttft_seconds",
        "next_question_e2e_seconds",
        "turn_latency_seconds",
        "sse_resume_seconds",
    )
    result = []
    for _, cohort_samples in _group_by_cohort(samples):
        result.append(
            {
                "cohort": _cohort_dict(cohort_samples[0]),
                "sample_count": len(cohort_samples),
                "latency": {
                    field: _stats(
                        [
                            float(getattr(item, field))
                            for item in cohort_samples
                            if getattr(item, field) is not None
                        ]
                    )
                    for field in fields
                },
                "input_tokens": sum(item.input_tokens for item in cohort_samples),
                "output_tokens": sum(item.output_tokens for item in cohort_samples),
                "cached_input_tokens": sum(
                    item.cached_input_tokens for item in cohort_samples
                ),
                "planned_provider_requests": sum(
                    item.planned_provider_requests for item in cohort_samples
                ),
                "actual_provider_requests": sum(
                    item.actual_provider_requests for item in cohort_samples
                ),
                "retry_count": sum(item.retry_count for item in cohort_samples),
                "fallback_count": sum(item.fallback_count for item in cohort_samples),
                "estimated_cost": sum(
                    item.estimated_cost or 0.0 for item in cohort_samples
                ),
            }
        )
    return result


def _fixed_adaptive_comparisons(samples) -> list[dict[str, Any]]:
    adaptive_groups = _group_by_cohort(
        [
            item
            for item in samples
            if item.policy_version == "adaptive_v1"
            and item.first_or_recovery == "first"
            and item.followup_or_next_question == "follow_up"
        ]
    )
    result = []
    for _, adaptive in adaptive_groups:
        fixed = _matching_fixed_baseline(samples, adaptive[0])
        adaptive_values = [
            item.followup_e2e_ttft_seconds
            for item in adaptive
            if item.followup_e2e_ttft_seconds is not None
        ]
        fixed_values = [
            item.followup_e2e_ttft_seconds
            for item in fixed
            if item.followup_e2e_ttft_seconds is not None
        ]
        fixed_p95 = nearest_rank(fixed_values, 0.95) if fixed_values else None
        adaptive_p95 = nearest_rank(adaptive_values, 0.95) if adaptive_values else None
        result.append(
            {
                "matching_dimensions": {
                    key: value
                    for key, value in _cohort_dict(adaptive[0]).items()
                    if key != "fixed_or_adaptive"
                },
                "fixed_sample_count": len(fixed_values),
                "adaptive_sample_count": len(adaptive_values),
                "fixed_followup_e2e_ttft_p95_seconds": fixed_p95,
                "adaptive_followup_e2e_ttft_p95_seconds": adaptive_p95,
                "adaptive_to_fixed_p95_ratio": (
                    adaptive_p95 / fixed_p95
                    if adaptive_p95 is not None and fixed_p95 not in {None, 0}
                    else None
                ),
                "fixed_decision_latency_seconds": None,
                "fixed_usage": _usage_totals(fixed),
                "adaptive_usage": _usage_totals(adaptive),
            }
        )
    return result


def _usage_totals(samples) -> dict[str, float | int]:
    return {
        "input_tokens": sum(item.input_tokens for item in samples),
        "output_tokens": sum(item.output_tokens for item in samples),
        "cached_input_tokens": sum(item.cached_input_tokens for item in samples),
        "provider_requests": sum(item.actual_provider_requests for item in samples),
        "retry_count": sum(item.retry_count for item in samples),
        "fallback_count": sum(item.fallback_count for item in samples),
        "estimated_cost": sum(item.estimated_cost or 0.0 for item in samples),
    }


def _session_usage(samples) -> list[dict[str, Any]]:
    grouped: dict[str, list[FollowupPerformanceSample]] = defaultdict(list)
    for sample in samples:
        grouped[sample.session_id].append(sample)
    result = []
    for session_id, session_samples in sorted(grouped.items()):
        first = [item for item in session_samples if item.first_or_recovery == "first"]
        question_count = max(item.question_count for item in session_samples)
        followup_count = sum(
            item.followup_or_next_question == "follow_up" for item in first
        )
        input_tokens = sum(item.input_tokens for item in first)
        output_tokens = sum(item.output_tokens for item in first)
        provider_requests = sum(item.actual_provider_requests for item in first)
        result.append(
            {
                "session_id": session_id,
                "question_count": question_count,
                "actual_followup_count": followup_count,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cached_input_tokens": sum(
                    item.cached_input_tokens for item in first
                ),
                "provider_requests": provider_requests,
                "estimated_cost": sum(item.estimated_cost or 0.0 for item in first),
                "input_tokens_per_question": input_tokens / question_count,
                "output_tokens_per_question": output_tokens / question_count,
                "provider_requests_per_question": provider_requests / question_count,
                "provider_requests_per_followup": (
                    provider_requests / followup_count if followup_count else None
                ),
            }
        )
    return result


def _anomaly_cases(samples, gate_results) -> list[dict[str, Any]]:
    failures = [
        item
        for item in gate_results
        if item["status"] in {"FAIL", "INSUFFICIENT_SAMPLE", "INSUFFICIENT_BASELINE"}
    ]
    latency_fields = (
        "decision_latency_seconds",
        "generation_ttft_seconds",
        "generation_complete_seconds",
        "followup_e2e_ttft_seconds",
        "next_question_e2e_seconds",
        "turn_latency_seconds",
        "sse_resume_seconds",
    )
    maxima = []
    for field in latency_fields:
        available = [sample for sample in samples if getattr(sample, field) is not None]
        if not available:
            continue
        maximum = max(float(getattr(sample, field)) for sample in available)
        maxima.append(
            {
                "metric": field,
                "maximum": maximum,
                "sample_ids": [
                    sample.sample_id
                    for sample in available
                    if float(getattr(sample, field)) == maximum
                ],
            }
        )
    return [{"gate_failures": failures, "latency_maxima": maxima}]


def build_synthetic_performance_artifact(
    *,
    samples_per_cohort: int = 30,
) -> FollowupPerformanceArtifact:
    if samples_per_cohort < 30:
        raise ValueError("synthetic gate fixture requires at least 30 samples per cohort")
    samples: list[FollowupPerformanceSample] = []
    for startup in ("cold", "warm"):
        startup_delta = 0.10 if startup == "cold" else 0.0
        for index in range(samples_per_cohort):
            suffix = f"{startup}-{index:03d}"
            # The fixture is deliberately a Gate-contract fixture, not a claim
            # about real latency. Its fixed path is slower than the adaptive
            # two-stage path so the comparable-baseline branch has a passing
            # example; real Provider evidence is required for Quality PASS.
            fixed_ttft = 1.00 + startup_delta + (index % 5) * 0.005
            samples.append(
                FollowupPerformanceSample(
                    sample_id=f"fixed-followup-{suffix}",
                    session_id=f"fixed-session-{suffix}",
                    question_id="q-fixed-followup",
                    policy_version="fixed_v1",
                    cold_or_warm=startup,
                    followup_or_next_question="follow_up",
                    first_or_recovery="first",
                    schema_version="interview-state-v1",
                    question_count=3,
                    provider_path="deepseek-openai-compatible",
                    source_kind="synthetic_fixture",
                    generation_ttft_seconds=fixed_ttft,
                    generation_complete_seconds=fixed_ttft + 0.30,
                    followup_e2e_ttft_seconds=fixed_ttft + 0.05,
                    turn_latency_seconds=fixed_ttft + 0.35,
                    input_tokens=500,
                    output_tokens=45,
                    cached_input_tokens=200 if startup == "warm" else 0,
                    followup_output_tokens=45,
                    planned_provider_requests=1,
                    actual_provider_requests=1,
                    provider_calls_per_answer=1,
                    provider_calls_per_main_question=1,
                    estimated_cost=0.001,
                )
            )
            decision = 0.45 + startup_delta + (index % 5) * 0.005
            generation_ttft = 0.35 + startup_delta + (index % 5) * 0.005
            samples.append(
                FollowupPerformanceSample(
                    sample_id=f"adaptive-followup-{suffix}",
                    session_id=f"adaptive-session-{suffix}",
                    question_id="q-adaptive-followup",
                    policy_version="adaptive_v1",
                    cold_or_warm=startup,
                    followup_or_next_question="follow_up",
                    first_or_recovery="first",
                    schema_version="interview-state-v1",
                    question_count=3,
                    provider_path="deepseek-openai-compatible",
                    source_kind="synthetic_fixture",
                    decision_latency_seconds=decision,
                    generation_ttft_seconds=generation_ttft,
                    generation_complete_seconds=generation_ttft + 0.35,
                    followup_e2e_ttft_seconds=decision + generation_ttft + 0.05,
                    turn_latency_seconds=decision + generation_ttft + 0.40,
                    input_tokens=800,
                    output_tokens=75,
                    cached_input_tokens=300 if startup == "warm" else 0,
                    decision_output_tokens=30,
                    followup_output_tokens=45,
                    planned_provider_requests=2,
                    actual_provider_requests=2,
                    provider_calls_per_answer=2,
                    provider_calls_per_main_question=4,
                    estimated_cost=0.0016,
                )
            )
            samples.append(
                FollowupPerformanceSample(
                    sample_id=f"adaptive-next-{suffix}",
                    session_id=f"adaptive-session-{suffix}",
                    question_id="q-adaptive-next",
                    policy_version="adaptive_v1",
                    cold_or_warm=startup,
                    followup_or_next_question="next_question",
                    first_or_recovery="first",
                    schema_version="interview-state-v1",
                    question_count=3,
                    provider_path="deepseek-openai-compatible",
                    source_kind="synthetic_fixture",
                    decision_latency_seconds=decision,
                    next_question_e2e_seconds=decision + 0.05,
                    turn_latency_seconds=decision + 0.05,
                    input_tokens=300,
                    output_tokens=30,
                    cached_input_tokens=100 if startup == "warm" else 0,
                    decision_output_tokens=30,
                    planned_provider_requests=1,
                    actual_provider_requests=1,
                    provider_calls_per_answer=1,
                    provider_calls_per_main_question=4,
                    estimated_cost=0.0006,
                )
            )
            samples.append(
                FollowupPerformanceSample(
                    sample_id=f"terminal-next-{suffix}",
                    session_id=f"adaptive-session-{suffix}",
                    question_id="q-terminal-next",
                    policy_version="adaptive_v1",
                    cold_or_warm=startup,
                    followup_or_next_question="next_question",
                    first_or_recovery="first",
                    schema_version="interview-state-v1",
                    question_count=3,
                    provider_path="deepseek-openai-compatible",
                    source_kind="synthetic_fixture",
                    followup_count_before=2,
                    next_question_e2e_seconds=0.05,
                    turn_latency_seconds=0.05,
                    planned_provider_requests=0,
                    actual_provider_requests=0,
                    provider_calls_per_answer=0,
                    provider_calls_per_main_question=4,
                    estimated_cost=0.0,
                )
            )
            for policy in ("fixed_v1", "adaptive_v1"):
                samples.append(
                    FollowupPerformanceSample(
                        sample_id=f"{policy}-recovery-{suffix}",
                        session_id=f"{policy}-session-{suffix}",
                        question_id=f"q-{policy}-recovery",
                        policy_version=policy,
                        cold_or_warm=startup,
                        followup_or_next_question="follow_up",
                        first_or_recovery="recovery",
                        schema_version="interview-state-v1",
                        question_count=3,
                        provider_path="deepseek-openai-compatible",
                        source_kind="synthetic_fixture",
                        sse_resume_seconds=0.20 + startup_delta + (index % 5) * 0.005,
                    )
                )
    return FollowupPerformanceArtifact(
        source_kind="synthetic_fixture",
        samples=samples,
    )
