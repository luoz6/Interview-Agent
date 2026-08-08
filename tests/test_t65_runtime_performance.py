from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import scripts.run_t65_runtime_performance as runtime_cli

from app.services.agent_runtime import AgentRunRecord
from app.services.followup_performance import (
    PerformancePricingSnapshot,
    SseRecoveryMeasurement,
)
from app.services.followup_provider_preflight import (
    DeepSeekDiscoverySnapshot,
    ProviderPrice,
)
from app.services.interview_quality_gate import load_gate_config
from app.services.t65_runtime_performance import (
    CapturedTimingBoundaries,
    CapturingAgentRunRecorder,
    RuntimeCohortSpec,
    T65RuntimeCapturePlan,
    build_runtime_performance_evidence,
    correlate_runtime_boundaries,
    validate_capture_plan,
)


SHA = "a" * 64


@pytest.mark.parametrize(
    "run_id",
    ["..", "../outside", r"C:\outside\run", r"\\server\share\run"],
)
def test_t65_runtime_cli_rejects_unsafe_run_id_before_provider_or_write(
    monkeypatch, tmp_path, run_id
):
    plan_path, execution_path = _write_cli_inputs(tmp_path)
    output_root = tmp_path / "safe"

    with pytest.raises(SystemExit, match="invalid --run-id"):
        runtime_cli.main(
            [
                "--capture-plan",
                str(plan_path),
                "--execution-manifest",
                str(execution_path),
                "--context-window-tokens",
                "128000",
                "--out",
                str(output_root),
                "--run-id",
                run_id,
            ],
            capture_executor=lambda *_args, **_kwargs: pytest.fail(
                "unsafe run-id must stop before capture"
            ),
            provider_sender=lambda *_args, **_kwargs: pytest.fail(
                "unsafe run-id must stop before Provider"
            ),
            discovery_executor=lambda **_kwargs: pytest.fail(
                "unsafe run-id must stop before discovery"
            ),
        )

    assert not output_root.exists()


def _cohort(
    policy: str,
    path: str,
    execution: str = "first",
    *,
    samples: int = 30,
) -> RuntimeCohortSpec:
    return RuntimeCohortSpec(
        cold_or_warm="warm",
        fixed_or_adaptive=policy,
        followup_or_next_question=path,
        first_or_recovery=execution,
        schema_version="interview-runtime-v1",
        question_count=8,
        provider_path="local-api-sse",
        target_samples=samples,
    )


def _plan(*cohorts: RuntimeCohortSpec) -> T65RuntimeCapturePlan:
    return T65RuntimeCapturePlan(
        plan_id="t65-runtime-test",
        candidate_revision="1" * 40,
        candidate_tree="2" * 40,
        gate_config_sha256="3" * 64,
        authorization_sha256="4" * 64,
        cohorts=list(cohorts),
    )


def _agent_record(*, first_item_ms: float = 120.0) -> AgentRunRecord:
    return AgentRunRecord(
        correlation_id="corr-1",
        agent="examiner",
        operation="stream_followup_attempt",
        phase="interview",
        session_id="session-1",
        question_id="question-1",
        command_id="command-1",
        status="completed",
        started_at="2026-08-07T00:00:00Z",
        finished_at="2026-08-07T00:00:01Z",
        latency_ms=250,
        output_type="stream",
        safe_metadata={
            "first_item_latency_ms": first_item_ms,
            "provider_attempt_count": 1,
        },
    )


def _pricing() -> PerformancePricingSnapshot:
    return PerformancePricingSnapshot(
        source_url="https://api-docs.deepseek.com/quick_start/pricing",
        observed_at="2026-08-07T00:00:00Z",
        cache_hit_input_per_million=0.1,
        cache_miss_input_per_million=0.2,
        output_per_million=0.3,
    )


def test_runtime_capture_keeps_provider_first_item_and_first_visible_sse_distinct():
    capture = correlate_runtime_boundaries(
        sample_id="sample-1",
        session_id="session-1",
        question_id="question-1",
        command_id="command-1",
        cohort=_cohort("adaptive_v1", "follow_up"),
        external_stopwatch={
            "followup_first_visible_seconds": 0.31,
            "generation_complete_seconds": 0.42,
            "turn_complete_seconds": 0.48,
        },
        decision_duration_ms=80,
        agent_record=_agent_record(first_item_ms=120),
        sse_measurement=None,
        provider_trace_id_sha256s=[SHA],
        usage={
            "provider_attempt_count": 1,
            "provider_metered_attempt_count": 1,
            "provider_input_tokens": 100,
            "provider_output_tokens": 20,
            "provider_cached_input_tokens": 10,
            "decision_output_tokens": 5,
            "followup_output_tokens": 15,
        },
    )
    assert capture.provider_first_item_seconds == 0.12
    assert capture.followup_first_visible_seconds == 0.31
    assert capture.provider_first_item_seconds != capture.followup_first_visible_seconds
    assert capture.timing_sources["provider_first_item"] != capture.timing_sources[
        "followup_first_visible"
    ]


def test_runtime_capture_correlates_agent_record_by_session_command_and_operation():
    recorder = CapturingAgentRunRecorder()
    record = _agent_record()
    recorder.record(record)
    assert recorder.one(
        session_id="session-1",
        command_id="command-1",
        operation="stream_followup_attempt",
    ).run_id == record.run_id
    with pytest.raises(ValueError, match="exactly one"):
        recorder.one(
            session_id="session-1",
            command_id="wrong",
            operation="stream_followup_attempt",
        )


def test_runtime_recorder_rejects_unsafe_metadata_without_copying_values():
    recorder = CapturingAgentRunRecorder()
    record = _agent_record().model_copy(
        update={"safe_metadata": {"first_item_latency_ms": 10, "prompt": "secret"}}
    )
    recorder.record(record)
    assert recorder.records[0].safe_metadata == {"first_item_latency_ms": 10}


def test_runtime_recovery_requires_proven_zero_provider_calls():
    measurement = SseRecoveryMeasurement(
        disconnected_after_event_count=1,
        resumed_event_count=2,
        resume_seconds=0.02,
        last_event_id_before_disconnect="g1:1:1",
        first_resumed_event_id="g1:1:2",
    )
    with pytest.raises(ValueError, match="zero Provider usage"):
        correlate_runtime_boundaries(
            sample_id="recovery-1",
            session_id="session-1",
            question_id="question-1",
            command_id="command-1",
            cohort=_cohort("adaptive_v1", "follow_up", "recovery"),
            external_stopwatch={},
            decision_duration_ms=None,
            agent_record=None,
            sse_measurement=measurement,
            provider_trace_id_sha256s=[],
            usage={"provider_attempt_count": 1},
        )


def test_runtime_missing_cached_usage_remains_null_and_incomplete():
    capture = correlate_runtime_boundaries(
        sample_id="sample-missing-cache",
        session_id="session-1",
        question_id="question-1",
        command_id="command-1",
        cohort=_cohort("adaptive_v1", "next_question"),
        external_stopwatch={
            "next_question_visible_seconds": 0.08,
            "turn_complete_seconds": 0.1,
        },
        decision_duration_ms=50,
        agent_record=_agent_record(),
        sse_measurement=None,
        provider_trace_id_sha256s=[SHA],
        usage={
            "provider_attempt_count": 1,
            "provider_metered_attempt_count": 1,
            "provider_input_tokens": 100,
            "provider_output_tokens": 20,
        },
    )
    assert capture.capture_complete is False
    assert capture.cached_input_tokens is None


def test_runtime_capture_plan_rejects_small_cohort_and_missing_fixed_baseline():
    gate = load_gate_config()
    with pytest.raises(ValueError, match="at least 30"):
        validate_capture_plan(
            _plan(_cohort("adaptive_v1", "next_question", samples=29)),
            gate,
        )
    with pytest.raises(ValueError, match="fixed_v1 baseline"):
        validate_capture_plan(
            _plan(_cohort("adaptive_v1", "follow_up")),
            gate,
        )


def test_runtime_capture_incomplete_boundary_returns_observability_not_complete_artifact():
    fixed = _cohort("fixed_v1", "follow_up")
    adaptive = _cohort("adaptive_v1", "follow_up")
    plan = _plan(fixed, adaptive)
    capture = CapturedTimingBoundaries(
        sample_id="incomplete-1",
        session_id="session-1",
        question_id="question-1",
        command_id="command-1",
        cohort=adaptive,
        capture_complete=False,
        provider_attempts=1,
        provider_metered_attempts=1,
        retries=0,
        input_tokens=100,
        output_tokens=20,
        cached_input_tokens=0,
        provider_trace_id_sha256s=[SHA],
    )
    result = build_runtime_performance_evidence(
        plan=plan,
        captures=[capture],
        pricing=_pricing(),
        source_capture_sha256="5" * 64,
        provider_name="DeepSeek_OPENAI_COMPATIBLE",
        model_id="deepseek-v4-pro",
        capture_run_id="run-1",
        gate_config=load_gate_config(),
    )
    assert result.status == "BLOCKED_NOT_OBSERVABLE"
    assert result.performance_artifact is None
    assert result.observability is not None
    assert "PERFORMANCE_SIGNAL_NOT_OBSERVABLE" in result.hard_stop_conditions
    unavailable = [
        signal
        for signal in result.observability.signals
        if signal.status == "not_observable"
    ]
    assert unavailable
    assert all(signal.seconds is None for signal in unavailable)


def test_runtime_full_requires_exact_cohorts_thirty_samples_and_zero_call_recovery():
    cohorts = (
        _cohort("fixed_v1", "follow_up"),
        _cohort("adaptive_v1", "follow_up"),
        _cohort("adaptive_v1", "next_question"),
        _cohort("adaptive_v1", "next_question", "recovery"),
    )
    plan = _plan(*cohorts)
    captures = [
        _complete_capture(cohort, index)
        for cohort in cohorts
        for index in range(cohort.target_samples)
    ]
    result = build_runtime_performance_evidence(
        plan=plan,
        captures=captures,
        pricing=_pricing(),
        source_capture_sha256="5" * 64,
        provider_name="DeepSeek_OPENAI_COMPATIBLE",
        model_id="deepseek-v4-pro",
        capture_run_id="run-full",
        gate_config=load_gate_config(),
    )
    assert result.status == "COMPLETE"
    assert result.performance_artifact is not None
    assert len(result.performance_artifact.samples) == 120
    recovery = [
        sample
        for sample in result.performance_artifact.samples
        if sample.first_or_recovery == "recovery"
    ]
    assert recovery
    assert all(sample.actual_provider_requests == 0 for sample in recovery)
    assert all(sample.estimated_cost == 0 for sample in recovery)


def test_runtime_cli_fixture_is_diagnostic_and_never_discovers_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    plan_path, execution_path = _write_cli_inputs(tmp_path)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "must-not-be-persisted")
    monkeypatch.setenv("POSTGRES_DSN", "postgresql://must-not-be-persisted")

    def forbidden_discovery(**_kwargs):
        raise AssertionError("fixture mode must not discover or call Provider")

    exit_code = runtime_cli.main(
        [
            "--capture-plan",
            str(plan_path),
            "--execution-manifest",
            str(execution_path),
            "--context-window-tokens",
            "128000",
            "--provider-mode",
            "fixture",
            "--out",
            str(tmp_path / "runs"),
            "--run-id",
            "fixture-diagnostic",
        ],
        discovery_executor=forbidden_discovery,
        identity_resolver=lambda: ("1" * 40, "2" * 40, True),
    )

    assert exit_code == 2
    manifest_path = tmp_path / "runs" / "fixture-diagnostic" / "manifest.json"
    manifest_text = manifest_path.read_text(encoding="utf-8")
    manifest = json.loads(manifest_text)
    assert manifest["quality_status"] == "BLOCKED_SYNTHETIC_FIXTURE_ONLY"
    assert "must-not-be-persisted" not in manifest_text
    assert manifest["postgres_dsn_present"] is True


def test_runtime_cli_wrong_context_stops_before_discovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    plan_path, execution_path = _write_cli_inputs(tmp_path)
    monkeypatch.setenv("POSTGRES_DSN", "postgresql://not-persisted")

    def forbidden_discovery(**_kwargs):
        raise AssertionError("invalid context must stop before discovery")

    exit_code = runtime_cli.main(
        [
            "--capture-plan",
            str(plan_path),
            "--execution-manifest",
            str(execution_path),
            "--context-window-tokens",
            "64000",
            "--provider-mode",
            "provider",
            "--out",
            str(tmp_path / "runs"),
            "--run-id",
            "wrong-context",
        ],
        capture_executor=lambda _request: pytest.fail("capture must not run"),
        discovery_executor=forbidden_discovery,
        identity_resolver=lambda: ("1" * 40, "2" * 40, True),
    )
    assert exit_code == 2
    manifest = json.loads(
        (tmp_path / "runs" / "wrong-context" / "manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert "CONTEXT_WINDOW_CAPABILITY_UNAVAILABLE" in manifest["hard_stop_conditions"]


def test_runtime_cli_candidate_mismatch_stops_before_discovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    plan_path, execution_path = _write_cli_inputs(tmp_path)
    monkeypatch.setenv("POSTGRES_DSN", "postgresql://not-persisted")

    def forbidden_discovery(**_kwargs):
        raise AssertionError("candidate mismatch must stop before discovery")

    exit_code = runtime_cli.main(
        [
            "--capture-plan",
            str(plan_path),
            "--execution-manifest",
            str(execution_path),
            "--context-window-tokens",
            "128000",
            "--provider-mode",
            "provider",
            "--out",
            str(tmp_path / "runs"),
            "--run-id",
            "candidate-mismatch",
        ],
        capture_executor=lambda _request: pytest.fail("capture must not run"),
        discovery_executor=forbidden_discovery,
        identity_resolver=lambda: ("f" * 40, "e" * 40, True),
    )
    assert exit_code == 2
    manifest = json.loads(
        (tmp_path / "runs" / "candidate-mismatch" / "manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert "PROVIDER_CANDIDATE_MISMATCH" in manifest["hard_stop_conditions"]


def test_runtime_cli_report_completion_gate_requires_independent_observation():
    plan = _plan_with_report_baseline()
    gate_result, stop = runtime_cli._report_completion_gate(
        None,
        plan=plan,
        gate=load_gate_config(),
    )
    assert gate_result is None
    assert stop == "PERFORMANCE_SIGNAL_NOT_OBSERVABLE"


def test_runtime_cli_report_completion_gate_passes_only_frozen_comparable_capture():
    plan = _plan_with_report_baseline()
    gate_result, stop = runtime_cli._report_completion_gate(
        {
            "capture_complete": True,
            "comparable_baseline": True,
            "sample_count": 30,
            "p95_seconds": 90.0,
            "baseline_p95_seconds": 100.0,
            "baseline_artifact_sha256": "b" * 64,
        },
        plan=plan,
        gate=load_gate_config(),
    )
    assert stop is None
    assert gate_result is not None
    assert gate_result["status"] == "PASS"
    assert gate_result["metric_key"] == "operations.report_completion_p95_seconds"


def test_runtime_cli_report_completion_gate_reports_real_failure():
    plan = _plan_with_report_baseline()
    gate_result, stop = runtime_cli._report_completion_gate(
        {
            "capture_complete": True,
            "comparable_baseline": True,
            "sample_count": 30,
            "p95_seconds": 121.0,
            "baseline_p95_seconds": 200.0,
            "baseline_artifact_sha256": "b" * 64,
        },
        plan=plan,
        gate=load_gate_config(),
    )
    assert stop is None
    assert gate_result is not None
    assert gate_result["status"] == "FAIL"


def test_runtime_cli_transport_fsyncs_attempt_before_sender_and_never_logs_payload(
    tmp_path: Path,
):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    manifest = {
        "run_id": "transport-test",
        "task": "T65",
        "scope": "full",
        "authorization_id": "auth-test",
        "authorization_sha256": "a" * 64,
        "candidate_revision": "1" * 40,
        "candidate_tree": "2" * 40,
        "provider_called": False,
    }
    runtime_cli._write_json(run_dir / "manifest.json", manifest)
    request = {"candidate_answer": "secret-answer-must-not-be-logged"}

    def sender(received):
        ledger_path = run_dir / "provider-attempt-ledger.jsonl"
        assert ledger_path.is_file()
        ledger_text = ledger_path.read_text(encoding="utf-8")
        assert "ATTEMPT_START" in ledger_text
        assert request["candidate_answer"] not in ledger_text
        persisted = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
        assert persisted["provider_attempt_starts"] == 1
        assert persisted["first_data_request_sent"] is True
        assert received is request
        return {"ok": True}

    transport = runtime_cli._FsyncAttemptTransport(
        run_dir=run_dir,
        manifest=manifest,
        provider="DeepSeek",
        model="deepseek-v4-pro",
        sender=sender,
    )
    assert transport.send(request) == {"ok": True}
    assert transport.attempt_count == 1


def test_runtime_cli_normal_followup_cannot_claim_zero_provider_requests():
    capture = _complete_capture(_cohort("adaptive_v1", "follow_up"), 0).model_copy(
        update={
            "provider_attempts": 0,
            "provider_metered_attempts": 0,
            "provider_trace_id_sha256s": [],
        }
    )
    assert runtime_cli._complete_provider_usage_contract([capture]) is False


def test_runtime_cli_recovery_accepts_explicit_zero_provider_usage():
    capture = _complete_capture(
        _cohort("adaptive_v1", "next_question", "recovery"),
        0,
    )
    assert runtime_cli._complete_provider_usage_contract([capture]) is True


def test_runtime_cli_frozen_source_capture_is_relative_and_hash_bound(tmp_path: Path):
    plan_path = tmp_path / "capture-plan.json"
    plan_path.write_text("{}", encoding="utf-8")
    source_path = tmp_path / "redacted-capture.json"
    source_path.write_text('{"captures":[]}\n', encoding="utf-8")
    source_sha = hashlib.sha256(source_path.read_bytes()).hexdigest()
    loaded = runtime_cli._load_frozen_source_capture(
        plan_path,
        {"path": source_path.name, "sha256": source_sha},
    )
    assert loaded["source_capture_sha256"] == source_sha
    with pytest.raises(ValueError, match="safe relative path"):
        runtime_cli._load_frozen_source_capture(
            plan_path,
            {"path": str(source_path.resolve()), "sha256": source_sha},
        )
    with pytest.raises(ValueError, match="hash mismatch"):
        runtime_cli._load_frozen_source_capture(
            plan_path,
            {"path": source_path.name, "sha256": "0" * 64},
        )


@pytest.mark.parametrize(
    ("scope", "report_p95", "expected_exit", "expected_status"),
    [
        ("full", 90.0, 2, "BLOCKED_DIAGNOSTIC_ONLY"),
        ("full", 121.0, 2, "BLOCKED_DIAGNOSTIC_ONLY"),
        ("smoke", 90.0, 2, "NOT_RUN_FULL_REQUIRED"),
    ],
)
def test_runtime_cli_injected_executor_can_never_formal_pass(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    scope: str,
    report_p95: float,
    expected_exit: int,
    expected_status: str,
):
    plan_path, execution_path = _write_cli_inputs(tmp_path)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "injected-test-key")
    monkeypatch.setenv("POSTGRES_DSN", "postgresql://injected-test-dsn")
    monkeypatch.setattr(runtime_cli, "_postgres_reachable", lambda _dsn: True)

    def capture_executor(context):
        captures = [
            _complete_capture(cohort, index)
            for cohort in context["plan"].cohorts
            for index in range(cohort.target_samples)
        ]
        captures = [_make_cli_gate_passing_capture(item) for item in captures]
        for capture in captures:
            for _ in range(capture.provider_attempts or 0):
                context["send_provider_request"](
                    {"sample_id": capture.sample_id, "redacted": True}
                )
        return {
            "captures": [item.model_dump(mode="json") for item in captures],
            "pricing_snapshot": _pricing().model_dump(mode="json"),
            "source_capture_sha256": "5" * 64,
            "provider_name": "DeepSeek",
            "model_id": "deepseek-v4-pro",
            "capture_run_id": f"capture-{scope}-{report_p95}",
            "report_completion": {
                "capture_complete": True,
                "comparable_baseline": True,
                "sample_count": 30,
                "p95_seconds": report_p95,
                "baseline_p95_seconds": 200.0,
                "baseline_artifact_sha256": context[
                    "plan"
                ].report_baseline_artifact_sha256,
            },
        }

    run_id = f"exit-{scope}-{int(report_p95)}"
    exit_code = runtime_cli.main(
        [
            "--capture-plan",
            str(plan_path),
            "--execution-manifest",
            str(execution_path),
            "--context-window-tokens",
            "128000",
            "--provider-mode",
            "provider",
            "--scope",
            scope,
            "--out",
            str(tmp_path / "runs"),
            "--run-id",
            run_id,
        ],
        capture_executor=capture_executor,
        provider_sender=lambda _request: {"ok": True},
        discovery_executor=lambda **_kwargs: _successful_discovery(),
        identity_resolver=lambda: ("1" * 40, "2" * 40, True),
    )
    assert exit_code == expected_exit
    manifest = json.loads(
        (tmp_path / "runs" / run_id / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["quality_status"] == expected_status
    assert manifest["evidence_origin"] == "injected_executor"
    assert manifest["production_executor_sha256"] is None
    assert manifest["formal_evidence_eligible"] is False
    ledger = (tmp_path / "runs" / run_id / "provider-attempt-ledger.jsonl")
    assert len(ledger.read_text(encoding="utf-8").splitlines()) == 90


@pytest.mark.parametrize(
    (
        "provider_mode",
        "scope",
        "origin",
        "executor_sha",
        "quality",
        "overall",
        "expected",
    ),
    [
        ("provider", "full", "builtin_production", "a" * 64, "PASS", "PASS", 0),
        ("provider", "full", "builtin_production", "a" * 64, "FAIL", "FAIL", 1),
        ("provider", "smoke", "builtin_production", "a" * 64, "PASS", "PASS", 2),
        ("fixture", "full", "builtin_production", "a" * 64, "PASS", "PASS", 2),
        ("provider", "full", "injected_executor", "a" * 64, "PASS", "PASS", 2),
        ("provider", "full", "saved_replay", "a" * 64, "PASS", "PASS", 2),
        ("provider", "full", "builtin_production", None, "PASS", "PASS", 2),
        ("provider", "full", "builtin_production", "bad", "PASS", "PASS", 2),
    ],
)
def test_runtime_cli_formal_exit_contract_is_pure_and_origin_bound(
    provider_mode: str,
    scope: str,
    origin: str,
    executor_sha: str | None,
    quality: str,
    overall: str,
    expected: int,
):
    assert runtime_cli._runtime_exit_contract(
        provider_mode=provider_mode,
        scope=scope,
        evidence_origin=origin,
        production_executor_sha256=executor_sha,
        quality_status=quality,
        overall_status=overall,
    ) == expected


def test_runtime_cli_dummy_postgres_dsn_cannot_pass_preflight(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    plan_path, execution_path = _write_cli_inputs(tmp_path)
    monkeypatch.setenv("POSTGRES_DSN", "postgresql://dummy.invalid/not-real")
    monkeypatch.setattr(runtime_cli, "_postgres_reachable", lambda _dsn: False)

    exit_code = runtime_cli.main(
        [
            "--capture-plan",
            str(plan_path),
            "--execution-manifest",
            str(execution_path),
            "--context-window-tokens",
            "128000",
            "--provider-mode",
            "provider",
            "--out",
            str(tmp_path / "runs"),
            "--run-id",
            "dummy-postgres",
        ],
        capture_executor=lambda _context: pytest.fail("capture must not run"),
        provider_sender=lambda _request: pytest.fail("sender must not run"),
        discovery_executor=lambda **_kwargs: pytest.fail("discovery must not run"),
        identity_resolver=lambda: ("1" * 40, "2" * 40, True),
    )
    assert exit_code == 2
    manifest = json.loads(
        (tmp_path / "runs" / "dummy-postgres" / "manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert "POSTGRES_UNAVAILABLE" in manifest["hard_stop_conditions"]
    assert manifest["postgres_dsn_present"] is True
    assert manifest["postgres_available"] is False


def test_runtime_cli_missing_builtin_production_executor_is_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    plan_path, execution_path = _write_cli_inputs(tmp_path)
    monkeypatch.setenv("POSTGRES_DSN", "postgresql://reachable-for-test")
    monkeypatch.setattr(runtime_cli, "_postgres_reachable", lambda _dsn: True)

    exit_code = runtime_cli.main(
        [
            "--capture-plan",
            str(plan_path),
            "--execution-manifest",
            str(execution_path),
            "--context-window-tokens",
            "128000",
            "--provider-mode",
            "provider",
            "--out",
            str(tmp_path / "runs"),
            "--run-id",
            "builtin-unavailable",
        ],
        discovery_executor=lambda **_kwargs: pytest.fail("discovery must not run"),
        identity_resolver=lambda: ("1" * 40, "2" * 40, True),
    )
    assert exit_code == 2
    manifest = json.loads(
        (tmp_path / "runs" / "builtin-unavailable" / "manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["evidence_origin"] == "builtin_unavailable"
    assert manifest["formal_evidence_eligible"] is False
    assert "PERFORMANCE_SIGNAL_NOT_OBSERVABLE" in manifest["hard_stop_conditions"]


def test_runtime_cli_saved_replay_can_never_formal_pass(
    tmp_path: Path,
):
    plan_path, execution_path = _write_cli_inputs(tmp_path)
    source_path = tmp_path / "saved-redacted-capture.json"
    source_path.write_text('{"captures":[]}\n', encoding="utf-8")
    raw_plan = json.loads(plan_path.read_text(encoding="utf-8"))
    raw_plan["source_capture"] = {
        "path": source_path.name,
        "sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
    }
    plan_path.write_text(json.dumps(raw_plan), encoding="utf-8")

    exit_code = runtime_cli.main(
        [
            "--capture-plan",
            str(plan_path),
            "--execution-manifest",
            str(execution_path),
            "--context-window-tokens",
            "128000",
            "--provider-mode",
            "provider",
            "--out",
            str(tmp_path / "runs"),
            "--run-id",
            "saved-replay",
        ],
        discovery_executor=lambda **_kwargs: pytest.fail("discovery must not run"),
        identity_resolver=lambda: ("1" * 40, "2" * 40, True),
    )
    assert exit_code == 2
    manifest = json.loads(
        (tmp_path / "runs" / "saved-replay" / "manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["evidence_origin"] == "saved_replay"
    assert manifest["formal_evidence_eligible"] is False
    assert "SOURCE_CAPTURE_INCOMPLETE" in manifest["hard_stop_conditions"]


def _complete_capture(
    cohort: RuntimeCohortSpec,
    index: int,
) -> CapturedTimingBoundaries:
    policy = cohort.fixed_or_adaptive
    path = cohort.followup_or_next_question
    execution = cohort.first_or_recovery
    suffix = f"{policy}-{path}-{execution}-{index}"
    if execution == "recovery":
        return CapturedTimingBoundaries(
            sample_id=f"sample-{suffix}",
            session_id=f"session-{suffix}",
            question_id=f"question-{suffix}",
            command_id=f"command-{suffix}",
            cohort=cohort,
            sse_resume_seconds=0.02,
            provider_attempts=0,
            provider_metered_attempts=0,
            retries=0,
            input_tokens=0,
            output_tokens=0,
            cached_input_tokens=0,
        )
    trace = hashlib.sha256(suffix.encode("utf-8")).hexdigest()
    common = {
        "sample_id": f"sample-{suffix}",
        "session_id": f"session-{suffix}",
        "question_id": f"question-{suffix}",
        "command_id": f"command-{suffix}",
        "cohort": cohort,
        "decision_complete_seconds": 0.05 if policy == "adaptive_v1" else None,
        "turn_complete_seconds": 0.3 if path == "follow_up" else 0.1,
        "provider_attempts": 1,
        "provider_metered_attempts": 1,
        "retries": 0,
        "input_tokens": 100,
        "output_tokens": 20,
        "cached_input_tokens": 10,
        "decision_output_tokens": 5 if policy == "adaptive_v1" else None,
        "followup_output_tokens": 10 if path == "follow_up" else None,
        "provider_trace_id_sha256s": [trace],
    }
    if path == "follow_up":
        common.update(
            provider_first_item_seconds=0.1,
            followup_first_visible_seconds=0.2 if policy == "adaptive_v1" else 0.15,
            generation_complete_seconds=0.2,
        )
    else:
        common["next_question_visible_seconds"] = 0.08
    return CapturedTimingBoundaries(**common)


def _plan_with_report_baseline() -> T65RuntimeCapturePlan:
    return _plan(
        _cohort("fixed_v1", "follow_up"),
        _cohort("adaptive_v1", "follow_up"),
        _cohort("adaptive_v1", "next_question", samples=31),
        _cohort("adaptive_v1", "next_question", "recovery"),
    ).model_copy(
        update={
            "report_baseline_artifact": "report-baseline.json",
            "report_baseline_artifact_sha256": "b" * 64,
        }
    )


def _write_cli_inputs(tmp_path: Path) -> tuple[Path, Path]:
    gate_path = runtime_cli.DEFAULT_GATE
    authorization_path = runtime_cli.DEFAULT_AUTHORIZATION
    baseline_path = tmp_path / "report-baseline.json"
    baseline_path.write_text('{"frozen":true}\n', encoding="utf-8")
    plan = _plan_with_report_baseline().model_copy(
        update={
            "gate_config_sha256": hashlib.sha256(gate_path.read_bytes()).hexdigest(),
            "authorization_sha256": hashlib.sha256(
                authorization_path.read_bytes()
            ).hexdigest(),
            "report_baseline_artifact_sha256": hashlib.sha256(
                baseline_path.read_bytes()
            ).hexdigest(),
        }
    )
    plan_path = tmp_path / "capture-plan.json"
    plan_path.write_text(
        json.dumps(plan.model_dump(mode="json")),
        encoding="utf-8",
    )
    execution_path = tmp_path / "execution-manifest.json"
    execution_path.write_text(
        json.dumps(
            {
                "t65_authorization_revalidation": {
                    "provider_candidate_revision": plan.candidate_revision,
                    "provider_candidate_tree": plan.candidate_tree,
                }
            }
        ),
        encoding="utf-8",
    )
    return plan_path, execution_path


def _successful_discovery() -> DeepSeekDiscoverySnapshot:
    return DeepSeekDiscoverySnapshot(
        observed_at="2026-08-07T00:00:00Z",
        models_endpoint_ok=True,
        model_ids=["deepseek-v4-pro"],
        pricing_page_ok=True,
        prices={
            "deepseek-v4-pro": ProviderPrice(
                cache_hit_input_per_million=0.1,
                cache_miss_input_per_million=0.2,
                output_per_million=0.3,
            )
        },
    )


def _make_cli_gate_passing_capture(
    capture: CapturedTimingBoundaries,
) -> CapturedTimingBoundaries:
    update: dict[str, object] = {}
    if (
        capture.cohort.fixed_or_adaptive == "adaptive_v1"
        and capture.cohort.followup_or_next_question == "follow_up"
    ):
        update["followup_first_visible_seconds"] = 0.17
    if (
        capture.sample_id.endswith("adaptive_v1-next_question-first-0")
    ):
        update.update(
            {
                "followup_count_before": 2,
                "decision_complete_seconds": None,
                "provider_attempts": 0,
                "provider_metered_attempts": 0,
                "retries": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "cached_input_tokens": 0,
                "decision_output_tokens": None,
                "provider_trace_id_sha256s": [],
            }
        )
    if not update:
        return capture
    payload = capture.model_dump(mode="json")
    payload.update(update)
    return CapturedTimingBoundaries.model_validate(payload)
