import pytest
from pydantic import ValidationError

from app.services.followup_performance import (
    FollowupPerformanceArtifact,
    FollowupPerformanceSample,
    PerformancePricingSnapshot,
    build_synthetic_performance_artifact,
    evaluate_followup_performance,
    measure_sse_resume,
    nearest_rank,
)
from app.services.interview_event_stream import InterviewEventStreamService
from app.services.interview_quality_gate import load_gate_config


def _sample(**overrides):
    payload = {
        "sample_id": "sample-1",
        "session_id": "session-1",
        "question_id": "question-1",
        "policy_version": "adaptive_v1",
        "cold_or_warm": "warm",
        "followup_or_next_question": "follow_up",
        "first_or_recovery": "first",
        "schema_version": "interview-state-v1",
        "question_count": 3,
        "provider_path": "deepseek-openai-compatible",
        "source_kind": "synthetic_fixture",
        "decision_latency_seconds": 0.4,
        "generation_ttft_seconds": 0.3,
        "generation_complete_seconds": 0.6,
        "followup_e2e_ttft_seconds": 0.75,
        "turn_latency_seconds": 1.0,
        "input_tokens": 500,
        "output_tokens": 60,
        "cached_input_tokens": 100,
        "decision_output_tokens": 20,
        "followup_output_tokens": 40,
        "planned_provider_requests": 2,
        "actual_provider_requests": 2,
        "provider_calls_per_answer": 2,
        "provider_calls_per_main_question": 4,
    }
    payload.update(overrides)
    return FollowupPerformanceSample.model_validate(payload)


def _pricing():
    return PerformancePricingSnapshot(
        source_url="https://api-docs.deepseek.com/quick_start/pricing",
        observed_at="2026-08-05T00:00:00Z",
        cache_hit_input_per_million=0.5,
        cache_miss_input_per_million=1.0,
        output_per_million=2.0,
    )


def _real_samples(synthetic, *, capture_complete=True, omit_cost=False):
    pricing = _pricing()
    result = []
    for sample in synthetic.samples:
        estimated_cost = pricing.estimate(
            input_tokens=sample.input_tokens,
            output_tokens=sample.output_tokens,
            cached_input_tokens=sample.cached_input_tokens,
        )
        result.append(
            sample.model_copy(
                update={
                    "source_kind": "saved_provider_replay",
                    "provider_name": "DeepSeek",
                    "model_id": "authorized-model",
                    "capture_complete": capture_complete,
                    "provider_request_trace_ids": [
                        f"trace-{sample.sample_id}-{index}"
                        for index in range(sample.actual_provider_requests)
                    ],
                    "estimated_cost": (
                        None
                        if omit_cost and sample.actual_provider_requests > 0
                        else estimated_cost
                    ),
                }
            )
        )
    return result


def test_nearest_rank_uses_frozen_non_interpolated_definition():
    values = [1, 2, 3, 4, 5]

    assert nearest_rank(values, 0.50) == 3
    assert nearest_rank(values, 0.95) == 5
    assert nearest_rank(reversed(values), 1.0) == 5


def test_fixed_policy_cannot_fabricate_zero_decision_baseline():
    with pytest.raises(ValidationError, match="fixed_v1 has no Decision latency"):
        _sample(
            policy_version="fixed_v1",
            decision_latency_seconds=0.0,
            decision_output_tokens=None,
            planned_provider_requests=1,
            actual_provider_requests=1,
            provider_calls_per_answer=1,
        )


def test_recovery_is_separate_and_cannot_duplicate_provider_usage():
    with pytest.raises(ValidationError, match="SSE recovery cannot duplicate"):
        _sample(
            first_or_recovery="recovery",
            decision_latency_seconds=None,
            generation_ttft_seconds=None,
            generation_complete_seconds=None,
            followup_e2e_ttft_seconds=None,
            turn_latency_seconds=None,
            sse_resume_seconds=0.2,
        )


def test_second_followup_guard_is_zero_call_next_question():
    with pytest.raises(ValidationError, match="second follow-up guard must use zero"):
        _sample(
            followup_count_before=2,
            followup_or_next_question="next_question",
            decision_latency_seconds=None,
            generation_ttft_seconds=None,
            generation_complete_seconds=None,
            followup_e2e_ttft_seconds=None,
            followup_output_tokens=None,
            next_question_e2e_seconds=0.05,
            turn_latency_seconds=0.05,
        )


def test_token_and_fallback_accounting_is_internally_consistent():
    with pytest.raises(ValidationError, match="stage output tokens cannot exceed"):
        _sample(output_tokens=50, decision_output_tokens=20, followup_output_tokens=40)
    with pytest.raises(ValidationError, match="fallback_count cannot exceed"):
        _sample(fallback_count=3)
    with pytest.raises(ValidationError, match="finite number"):
        _sample(decision_latency_seconds=float("inf"))


def test_performance_contract_rejects_payload_fields():
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        _sample(prompt="candidate private answer")


def test_performance_identifiers_cannot_smuggle_free_text():
    with pytest.raises(ValidationError, match="String should match pattern"):
        _sample(sample_id="candidate private answer")


def test_synthetic_fixture_passes_engineering_but_not_provider_quality():
    artifact = build_synthetic_performance_artifact()

    result = evaluate_followup_performance(
        artifact,
        gate_config=load_gate_config(),
    )

    assert result["sample_count"] == 360
    assert result["engineering_status"] == "PASS"
    assert result["quality_status"] == "BLOCKED_NOT_RUN_REAL_PROVIDER"
    assert result["overall_status"] == "BLOCKED"
    assert result["fixed_decision_latency_baseline"] is None
    assert {
        item["status"] for item in result["gate_results"] if item["blocking"]
    } == {"PASS"}
    assert len(result["fixed_adaptive_same_path_comparisons"]) == 2
    assert all(
        item["fixed_decision_latency_seconds"] is None
        for item in result["fixed_adaptive_same_path_comparisons"]
    )


def test_latency_cohorts_do_not_mix_cold_and_warm_or_recovery():
    result = evaluate_followup_performance(
        build_synthetic_performance_artifact(),
        gate_config=load_gate_config(),
    )
    decision = [
        item
        for item in result["gate_results"]
        if item["metric_key"] == "operations.adaptive_decision_p95_seconds"
    ]
    resume = [
        item
        for item in result["gate_results"]
        if item["metric_key"] == "operations.sse_resume_p95_seconds"
    ]

    assert len(decision) == 4
    assert {item["cohort"]["cold_or_warm"] for item in decision} == {
        "cold",
        "warm",
    }
    assert all(item["cohort"]["first_or_recovery"] == "first" for item in decision)
    assert len(resume) == 4
    assert all(item["cohort"]["first_or_recovery"] == "recovery" for item in resume)


def test_missing_exact_fixed_cohort_is_insufficient_baseline_not_pass():
    artifact = build_synthetic_performance_artifact()
    artifact = artifact.model_copy(
        update={
            "samples": [
                sample
                for sample in artifact.samples
                if not (
                    sample.policy_version == "fixed_v1"
                    and sample.cold_or_warm == "warm"
                    and sample.first_or_recovery == "first"
                )
            ]
        }
    )

    result = evaluate_followup_performance(artifact, gate_config=load_gate_config())
    followup = [
        item
        for item in result["gate_results"]
        if item["metric_key"]
        == "operations.adaptive_followup_e2e_ttft_p95_seconds"
        and item["cohort"].get("cold_or_warm") == "warm"
    ]

    assert followup[0]["status"] == "INSUFFICIENT_BASELINE"
    assert result["automated_gate_status"] == "BLOCKED"


def test_gate_failure_is_reported_with_maximum_case_evidence():
    artifact = build_synthetic_performance_artifact()
    changed = artifact.samples[0].model_copy(update={"followup_output_tokens": 121})
    artifact = artifact.model_copy(update={"samples": [changed, *artifact.samples[1:]]})

    result = evaluate_followup_performance(artifact, gate_config=load_gate_config())
    output_gate = next(
        item
        for item in result["gate_results"]
        if item["metric_key"] == "operations.followup_output_tokens"
    )

    assert output_gate["status"] == "FAIL"
    assert result["engineering_status"] == "FAIL"
    assert result["overall_status"] == "FAIL"
    assert result["anomaly_cases"][0]["gate_failures"]


def test_complete_real_saved_capture_can_be_quality_eligible():
    synthetic = build_synthetic_performance_artifact()
    samples = _real_samples(synthetic)
    artifact = FollowupPerformanceArtifact(
        source_kind="saved_provider_replay",
        provider_name="DeepSeek",
        model_id="authorized-model",
        capture_run_id="provider-capture-1",
        source_capture_sha256="a" * 64,
        pricing_snapshot=_pricing(),
        samples=samples,
    )

    result = evaluate_followup_performance(artifact, gate_config=load_gate_config())

    assert result["quality_status"] == "PASS"
    assert result["overall_status"] == "PASS"


def test_hard_stopped_real_capture_never_masquerades_as_complete_quality():
    synthetic = build_synthetic_performance_artifact()
    samples = [
        sample.model_copy(
            update={
                "source_kind": "live_provider",
                "provider_name": "DeepSeek",
                "model_id": "authorized-model",
                "capture_complete": False,
                "provider_request_trace_ids": [
                    f"trace-{sample.sample_id}-{index}"
                    for index in range(sample.actual_provider_requests)
                ],
            }
        )
        for sample in synthetic.samples
    ]
    artifact = FollowupPerformanceArtifact(
        source_kind="live_provider",
        capture_status="hard_stopped",
        provider_name="DeepSeek",
        model_id="authorized-model",
        capture_run_id="provider-capture-stopped",
        source_capture_sha256="b" * 64,
        hard_stop_conditions=["MODEL_VERSION_DRIFT"],
        samples=samples,
    )

    result = evaluate_followup_performance(artifact, gate_config=load_gate_config())

    assert result["engineering_status"] == "PASS"
    assert result["quality_status"] == "BLOCKED_INCOMPLETE_PROVIDER_CAPTURE"
    assert result["overall_status"] == "BLOCKED"


def test_session_normalization_excludes_recovery_from_usage_totals():
    result = evaluate_followup_performance(
        build_synthetic_performance_artifact(),
        gate_config=load_gate_config(),
    )
    fixed_warm = next(
        item
        for item in result["session_usage"]
        if item["session_id"] == "fixed-session-warm-000"
    )

    assert fixed_warm["input_tokens"] == 500
    assert fixed_warm["actual_followup_count"] == 1
    assert fixed_warm["provider_requests_per_followup"] == 1


class _RecoveryGenerationStore:
    def get_by_source_command(self, session_id, command_id):
        return type("Generation", (), {"generation_id": "generation-1"})()

    def list_events_after(
        self,
        generation_id,
        *,
        after_attempt,
        after_sequence,
        limit,
    ):
        events = [
            type(
                "Event",
                (),
                {
                    "generation_id": generation_id,
                    "attempt_number": attempt,
                    "sequence": sequence,
                    "event_type": event_type,
                    "delta": f"private-chunk-{attempt}-{sequence}",
                },
            )()
            for attempt, sequence, event_type in (
                (1, 1, "chunk"),
                (1, 2, "chunk"),
                (2, 0, "generation_reset"),
                (2, 1, "chunk"),
            )
            if (attempt, sequence) > (after_attempt, after_sequence)
        ]
        return events[:limit]


class _StepClock:
    def __init__(self):
        self.value = 10.0

    def __call__(self):
        self.value += 0.025
        return self.value


def test_sse_disconnect_resume_uses_cursor_without_duplicate_or_payload_leak():
    service = InterviewEventStreamService(
        workflow_store=object(),
        generation_store=_RecoveryGenerationStore(),
        page_size=2,
    )

    measurement = measure_sse_resume(
        service,
        session_id="session-1",
        command_id="command-1",
        disconnect_after_events=2,
        clock=_StepClock(),
    )

    assert measurement.last_event_id_before_disconnect == "generation-1:1:2"
    assert measurement.first_resumed_event_id == "generation-1:2:0"
    assert measurement.resumed_event_count == 2
    assert measurement.duplicate_event_count == 0
    assert measurement.resume_seconds == pytest.approx(0.025)
    assert "private-chunk" not in measurement.model_dump_json()


def test_gate_config_cohort_drift_fails_closed():
    config = load_gate_config().model_copy(
        update={"cohort_dimensions": ["cold_or_warm"]}
    )

    with pytest.raises(ValueError, match="cohort dimensions drifted"):
        evaluate_followup_performance(
            build_synthetic_performance_artifact(),
            gate_config=config,
        )


def test_complete_real_provider_capture_requires_usage_and_cost_metering():
    synthetic = build_synthetic_performance_artifact()
    samples = _real_samples(synthetic, omit_cost=True)

    with pytest.raises(ValidationError, match="require token and cost metering"):
        FollowupPerformanceArtifact(
            source_kind="saved_provider_replay",
            provider_name="DeepSeek",
            model_id="authorized-model",
            capture_run_id="provider-capture-unmetered",
            source_capture_sha256="c" * 64,
            pricing_snapshot=_pricing(),
            samples=samples,
        )


def test_complete_real_provider_cost_must_match_frozen_price_snapshot():
    synthetic = build_synthetic_performance_artifact()
    samples = _real_samples(synthetic)
    samples[0] = samples[0].model_copy(update={"estimated_cost": 99.0})

    with pytest.raises(ValidationError, match="does not match the frozen pricing"):
        FollowupPerformanceArtifact(
            source_kind="saved_provider_replay",
            provider_name="DeepSeek",
            model_id="authorized-model",
            capture_run_id="provider-capture-bad-price",
            source_capture_sha256="d" * 64,
            pricing_snapshot=_pricing(),
            samples=samples,
        )
