from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import inspect
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Literal, Sequence

if not __package__:
    root = Path(__file__).resolve().parents[1]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

from app.services.followup_provider_preflight import (
    discover_deepseek_provider,
    estimate_provider_cost,
)
from app.services.interview_quality_gate import load_gate_config
from app.services.interview_quality_provider_authorization import (
    load_provider_authorization,
)
from app.services.llm import (
    LLMConfig,
    OpenAIInterviewLLM,
    REPORT_EVIDENCE_PROMPT_VERSION,
)
from app.services.prep import InterviewPlan, InterviewQuestion
from app.services.provider_usage import (
    consume_provider_context_metadata,
    reset_provider_context_metadata,
)
from app.services.report_eval_artifacts import EvaluationArtifactStore
from app.services.report_eval_metrics import (
    AttemptResult,
    calculate_metrics,
    ngram_coverage,
    normalize_text,
)
from app.services.report_rule_score import (
    REPORT_SCORING_RUBRIC_SHA256,
    REPORT_SCORING_RUBRIC_VERSION,
    applicable_dimensions_for_item,
)
from app.services.report_calibration_dataset import (
    CalibrationCase,
    CalibrationDataset,
    load_calibration_dataset,
)
from app.services.t65_provider_evidence import (
    SafeReportCaptureRecorder,
    SafeReportProviderAttempt,
    SafeReportProviderCapture,
    evaluate_t65_report_preflight,
)
from app.services.t65_formal_execution_receipt import validate_t65_formal_route


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = (
    ROOT / "tests" / "golden" / "interview_quality_v1" / "report-score-calibration-v1.json"
)
DEFAULT_DATASET_MANIFEST = DEFAULT_DATASET.with_suffix(".manifest.json")
DEFAULT_GATE_CONFIG = ROOT / "config" / "interview_quality_v1_gate.json"
DEFAULT_AUTHORIZATION = (
    ROOT / "config" / "interview_quality_v1_provider_authorization.json"
)
DEFAULT_EXECUTION_MANIFEST = ROOT / "docs" / "interview-quality-v1-execution-manifest.json"
DEFAULT_OUT = ROOT / "tmp" / "interview-quality-v1-provider-runs" / "report-scoring"
_DISCOVERY_ONLY_STOPS = frozenset(
    {"MODEL_VERSION_DRIFT", "USAGE_METERING_UNAVAILABLE"}
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the privacy-safe T65/T27 report-scoring benchmark"
    )
    parser.add_argument("--mode", choices=("provider", "saved-replay"), required=True)
    parser.add_argument("--scope", choices=("smoke", "full"), default="full")
    parser.add_argument("--purpose", choices=("evaluation",), default="evaluation")
    parser.add_argument(
        "--partition", choices=("dev", "blind-test", "all"), default="all"
    )
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument(
        "--dataset-manifest", type=Path, default=DEFAULT_DATASET_MANIFEST
    )
    parser.add_argument("--gate-config", type=Path, default=DEFAULT_GATE_CONFIG)
    parser.add_argument("--authorization", type=Path, default=DEFAULT_AUTHORIZATION)
    parser.add_argument(
        "--execution-manifest", type=Path, default=DEFAULT_EXECUTION_MANIFEST
    )
    parser.add_argument("--responses", type=Path)
    parser.add_argument("--case-id", action="append", default=[])
    parser.add_argument("--smoke-case-count", type=int, default=4)
    parser.add_argument("--runs-per-case", type=int, default=2)
    parser.add_argument("--context-window-tokens", type=int)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--run-id")
    parser.add_argument("--request-timeout-seconds", type=float, default=60.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _validate_args(args, parser)
    dataset_path = args.dataset.resolve()
    dataset_manifest_path = args.dataset_manifest.resolve()
    gate_path = args.gate_config.resolve()
    authorization_path = args.authorization.resolve()
    execution_path = args.execution_manifest.resolve()
    dataset = load_calibration_dataset(dataset_path)
    authorization = load_provider_authorization(authorization_path)
    candidate_revision, candidate_tree, worktree_clean = _candidate_identity()
    selected = _select_cases(
        dataset,
        partition=args.partition,
        case_ids=args.case_id,
        scope=args.scope,
        smoke_case_count=args.smoke_case_count,
    )
    runs_per_case = 1 if args.scope == "smoke" else args.runs_per_case
    run_id = args.run_id or _default_run_id()
    run_dir = args.out.resolve() / run_id
    if run_dir.exists():
        print(_status_json("BLOCKED", "EVIDENCE_PERSISTENCE_UNAVAILABLE"))
        return 2
    execution_payload = json.loads(execution_path.read_text(encoding="utf-8"))
    # Formal eligibility is independent from live-provider and engineering
    # completeness. The current verifier is deliberately fail-closed.
    formal_route_verified = validate_t65_formal_route(
        route="builtin_candidate", receipt=None
    )
    blind_released = _blind_partition_released(execution_payload)
    context_window = args.context_window_tokens or 128_000
    api_key = os.getenv("DEEPSEEK_API_KEY") or os.getenv("OPENAI_API_KEY")
    prompt_sha256 = _prompt_sha256()
    manifest: dict[str, Any] = {
        "schema_version": "t65-report-scoring-run-v1",
        "task": "T65",
        "constituent_task": "T27",
        "dimension": "report_scoring",
        "run_id": run_id,
        "created_at": _utc_now(),
        "updated_at": _utc_now(),
        "mode": args.mode,
        "scope": args.scope,
        "evidence_origin": (
            "live_provider" if args.mode == "provider" else "saved_replay"
        ),
        "formal_evidence_eligible": formal_route_verified,
        "engineering_evidence_complete": False,
        "purpose": args.purpose,
        "partition": args.partition,
        "candidate_revision": candidate_revision,
        "candidate_tree": candidate_tree,
        "worktree_clean": worktree_clean,
        "dataset_id": dataset.dataset_id,
        "dataset_sha256": _sha256(dataset_path),
        "dataset_manifest_sha256": _sha256(dataset_manifest_path),
        "gate_config_sha256": _sha256(gate_path),
        "authorization_sha256": _sha256(authorization_path),
        "execution_manifest_sha256": _sha256(execution_path),
        "prompt_version": REPORT_EVIDENCE_PROMPT_VERSION,
        "prompt_sha256": prompt_sha256,
        "rubric_version": REPORT_SCORING_RUBRIC_VERSION,
        "rubric_sha256": REPORT_SCORING_RUBRIC_SHA256,
        "selected_case_ids": [case.case_id for case in selected],
        "selected_case_count": len(selected),
        "runs_per_case": runs_per_case,
        "expected_attempt_count": len(selected) * runs_per_case,
        "planned_inference_requests": len(selected) * runs_per_case,
        "provider": authorization.provider.name,
        "model": authorization.provider.model_id,
        "environment_model_ignored": bool(
            os.getenv("OPENAI_MODEL")
            and os.getenv("OPENAI_MODEL") != authorization.provider.model_id
        ),
        "provider_called": False,
        "first_data_request_sent": False,
        "discovery_requests": 0,
        "inference_attempted": 0,
        "inference_metered": 0,
        "retries": 0,
        "input_tokens": None,
        "output_tokens": None,
        "cached_input_tokens": None,
        "estimated_cost": None,
        "hard_stop_conditions": [],
        "quality_status": "NOT_RUN",
        "decision": "RUNNING",
    }
    try:
        store = EvaluationArtifactStore.create(
            root=args.out.resolve(), run_id=run_id, manifest=manifest
        )
    except OSError:
        print(_status_json("BLOCKED", "EVIDENCE_PERSISTENCE_UNAVAILABLE"))
        return 2

    local_preflight = evaluate_t65_report_preflight(
        authorization=authorization,
        dataset=dataset,
        dataset_path=dataset_path,
        dataset_manifest_path=dataset_manifest_path,
        gate_config_path=gate_path,
        authorization_path=authorization_path,
        execution_manifest_path=execution_path,
        candidate_revision=candidate_revision,
        candidate_tree=candidate_tree,
        worktree_clean=worktree_clean,
        prompt_version=REPORT_EVIDENCE_PROMPT_VERSION,
        prompt_sha256=prompt_sha256,
        rubric_version=REPORT_SCORING_RUBRIC_VERSION,
        rubric_sha256=REPORT_SCORING_RUBRIC_SHA256,
        context_window_tokens=context_window,
        credential_present=bool(api_key) if args.mode == "provider" else True,
        evidence_persistence_available=True,
        discovery=None,
        partition=args.partition,
        blind_partition_released=blind_released,
    )
    manifest["local_preflight"] = local_preflight.model_dump(mode="json")
    store.write_manifest(manifest)
    local_stops = [
        item
        for item in local_preflight.hard_stop_conditions
        if item not in _DISCOVERY_ONLY_STOPS
    ]
    if local_stops:
        return _finish_blocked(store, manifest, local_stops)

    replay_capture: SafeReportProviderCapture | None = None
    gate_config = load_gate_config(gate_path)
    if args.mode == "provider":
        discovery = discover_deepseek_provider(
            api_key=api_key,
            timeout_seconds=args.request_timeout_seconds,
        )
    else:
        assert args.responses is not None
        replay_capture = SafeReportProviderCapture.model_validate_json(
            args.responses.read_text(encoding="utf-8")
        )
        discovery = replay_capture.pricing_snapshot
    manifest["discovery_requests"] = (
        discovery.model_request_attempts + discovery.pricing_request_attempts
        if args.mode == "provider"
        else 0
    )
    full_preflight = evaluate_t65_report_preflight(
        authorization=authorization,
        dataset=dataset,
        dataset_path=dataset_path,
        dataset_manifest_path=dataset_manifest_path,
        gate_config_path=gate_path,
        authorization_path=authorization_path,
        execution_manifest_path=execution_path,
        candidate_revision=candidate_revision,
        candidate_tree=candidate_tree,
        worktree_clean=worktree_clean,
        prompt_version=REPORT_EVIDENCE_PROMPT_VERSION,
        prompt_sha256=prompt_sha256,
        rubric_version=REPORT_SCORING_RUBRIC_VERSION,
        rubric_sha256=REPORT_SCORING_RUBRIC_SHA256,
        context_window_tokens=context_window,
        credential_present=bool(api_key) if args.mode == "provider" else True,
        evidence_persistence_available=True,
        discovery=discovery,
        partition=args.partition,
        blind_partition_released=blind_released,
    )
    manifest["provider_preflight"] = full_preflight.model_dump(mode="json")
    store.write_manifest(manifest)
    if not full_preflight.allowed:
        return _finish_blocked(
            store, manifest, list(full_preflight.hard_stop_conditions)
        )

    if args.mode == "provider":
        assert api_key is not None
        capture, attempt_results = _record_provider(
            selected,
            runs_per_case=runs_per_case,
            run_id=run_id,
            dataset=dataset,
            dataset_sha256=_sha256(dataset_path),
            authorization=authorization,
            authorization_sha256=_sha256(authorization_path),
            api_key=api_key,
            context_window_tokens=context_window,
            timeout_seconds=args.request_timeout_seconds,
            discovery=discovery,
            candidate_revision=candidate_revision,
            candidate_tree=candidate_tree,
            store=store,
            manifest=manifest,
            grounding_ngram_min_coverage=gate_config.algorithm_parameters[
                "evidence_ngram_min_coverage"
            ],
        )
        _write_json_atomic(
            store.run_dir / "local-redacted-provider-capture.json",
            capture.model_dump(mode="json"),
        )
        if capture.capture_status == "hard_stopped":
            manifest.update(_usage_manifest_fields(capture))
            return _finish_blocked(
                store, manifest, list(capture.hard_stop_conditions)
            )
    else:
        assert replay_capture is not None
        _validate_replay_capture(
            replay_capture,
            dataset=dataset,
            authorization_model=authorization.provider.model_id,
            candidate_revision=candidate_revision,
            candidate_tree=candidate_tree,
        )
        capture = replay_capture
        attempt_results = _replay_attempts(capture, selected)
        manifest["replay_provider_calls"] = 0

    expected_attempts = len(selected) * runs_per_case
    metrics = calculate_metrics(
        attempt_results,
        expected_attempt_count=expected_attempts,
        gate_config=gate_config,
    )
    metrics_payload = metrics.model_dump(mode="json")
    metrics_payload["blocking_failures"] = [
        {key: value for key, value in item.items() if key != "claim"}
        for item in metrics_payload["blocking_failures"]
    ]
    if args.mode == "saved-replay":
        _mark_replay_metrics_diagnostic(metrics_payload)
    store.write_metrics(metrics_payload)
    if args.mode == "provider":
        manifest.update(_usage_manifest_fields(capture))
    else:
        manifest.update(_replay_usage_manifest_fields(capture))
    manifest["completed_attempt_count"] = len(attempt_results)
    manifest["updated_at"] = _utc_now()
    if args.mode == "saved-replay":
        _mark_replay_diagnostic(manifest)
        store.write_manifest(manifest)
        print(
            _status_json(
                manifest["decision"], manifest["quality_status"], run_id
            )
        )
        return 2
    if args.scope == "smoke":
        manifest["decision"] = "SMOKE_COMPLETE_FULL_NOT_RUN"
        manifest["quality_status"] = "NOT_RUN_FULL_REQUIRED"
        store.write_manifest(manifest)
        print(_status_json(manifest["decision"], manifest["quality_status"], run_id))
        return 2
    complete_frozen_dataset = (
        args.partition == "all"
        and not args.case_id
        and len(selected) == len(dataset.cases)
        and runs_per_case == args.runs_per_case
    )
    manifest["engineering_evidence_complete"] = (
        args.mode == "provider"
        and complete_frozen_dataset
        and capture.capture_status == "complete"
        and capture.outbound_requests_attempted == capture.outbound_requests_metered
        and metrics.decision == "PASS"
    )
    if not complete_frozen_dataset:
        manifest["decision"] = "BLOCKED_PARTIAL_DATASET"
        manifest["quality_status"] = "BLOCKED_PARTIAL_DATASET"
        store.write_manifest(manifest)
        print(_status_json(manifest["decision"], manifest["quality_status"], run_id))
        return 2
    manifest["decision"] = metrics.decision
    manifest["quality_status"] = metrics.decision
    store.write_manifest(manifest)
    print(_status_json(manifest["decision"], manifest["quality_status"], run_id))
    if metrics.decision == "PASS":
        return 0
    if metrics.decision == "FAIL":
        return 1
    return 2


def _validate_args(
    args: argparse.Namespace, parser: argparse.ArgumentParser
) -> None:
    if args.mode == "provider" and args.responses is not None:
        parser.error("--responses is forbidden in provider mode")
    if args.mode == "saved-replay" and args.responses is None:
        parser.error("saved-replay mode requires --responses")
    if args.mode == "provider" and args.context_window_tokens is None:
        parser.error("provider mode requires --context-window-tokens=128000")
    if args.scope == "full" and args.mode == "provider" and args.case_id:
        parser.error("provider full mode forbids --case-id")
    if not 3 <= args.smoke_case_count <= 5:
        parser.error("--smoke-case-count must be between 3 and 5")
    if args.runs_per_case <= 0:
        parser.error("--runs-per-case must be positive")
    if args.request_timeout_seconds <= 0:
        parser.error("--request-timeout-seconds must be positive")


def _select_cases(
    dataset: CalibrationDataset,
    *,
    partition: str,
    case_ids: Sequence[str],
    scope: str,
    smoke_case_count: int,
) -> list[CalibrationCase]:
    cases = [
        case
        for case in dataset.cases
        if partition == "all"
        or (partition == "dev" and case.partition == "dev")
        or (partition == "blind-test" and case.partition == "blind")
    ]
    if case_ids:
        requested = set(case_ids)
        if len(requested) != len(case_ids):
            raise SystemExit("duplicate --case-id")
        selected = [case for case in cases if case.case_id in requested]
        if len(selected) != len(requested):
            raise SystemExit("unknown or out-of-partition --case-id")
        cases = selected
    if scope == "smoke":
        if len(cases) < smoke_case_count:
            raise SystemExit("smoke selection has too few cases")
        cases = cases[:smoke_case_count]
    if not cases:
        raise SystemExit("no calibration cases selected")
    return cases


def _record_provider(
    cases: Sequence[CalibrationCase],
    *,
    runs_per_case: int,
    run_id: str,
    dataset: CalibrationDataset,
    dataset_sha256: str,
    authorization,
    authorization_sha256: str,
    api_key: str,
    context_window_tokens: int,
    timeout_seconds: float,
    discovery,
    candidate_revision: str,
    candidate_tree: str,
    store: EvaluationArtifactStore,
    manifest: dict[str, Any],
    grounding_ngram_min_coverage: float,
) -> tuple[SafeReportProviderCapture, list[AttemptResult]]:
    attempts: list[SafeReportProviderAttempt] = []
    results: list[AttemptResult] = []
    hard_stops: list[str] = []
    durable_attempt_count = 0
    for case in cases:
        for run_number in range(1, runs_per_case + 1):
            def persist_attempt_start() -> None:
                nonlocal durable_attempt_count
                next_attempt = durable_attempt_count + 1
                _append_attempt_start(
                    store,
                    case_id=case.case_id,
                    run_number=run_number,
                    attempt_number=next_attempt,
                    authorization_id=authorization.authorization_id,
                    authorization_sha256=authorization_sha256,
                    provider=authorization.provider.name,
                    model=authorization.provider.model_id,
                    candidate_revision=candidate_revision,
                    candidate_tree=candidate_tree,
                )
                durable_attempt_count = next_attempt
                manifest["attempt_start_persisted"] = True
                manifest["provider_attempt_starts"] = durable_attempt_count
                manifest["updated_at"] = _utc_now()
                store.write_manifest(manifest)

            recorder = SafeReportCaptureRecorder()
            llm = OpenAIInterviewLLM(
                LLMConfig(
                    api_key=api_key,
                    model=authorization.provider.model_id,
                    base_url=authorization.provider.base_url,
                    temperature=0,
                    request_timeout_seconds=timeout_seconds,
                    max_retries=0,
                    context_window_tokens=context_window_tokens,
                ),
                trace_recorder=recorder,
                report_output_mode="structured_first",
                provider_attempt_hook=persist_attempt_start,
            )
            plan, evaluation_items = _case_to_input(case)
            reset_provider_context_metadata()
            started = time.perf_counter()
            try:
                report = llm.generate_report(
                    plan, evaluation_items, f"{run_id}-{case.case_id}-{run_number}"
                )
                elapsed = time.perf_counter() - started
                usage = consume_provider_context_metadata()
                provider_attempt_count = _optional_usage(
                    usage, "provider_attempt_count"
                )
                if provider_attempt_count is not None and provider_attempt_count > 0:
                    manifest["first_data_request_sent"] = True
                result = _report_attempt_result(
                    case,
                    report,
                    evaluation_items[0],
                    run_number=run_number,
                )
                safe_payload = {
                    "scoring_result": _safe_scoring_result(
                        result,
                        ngram_min_coverage=grounding_ngram_min_coverage,
                    ),
                    "provider_capture": recorder.consume(),
                }
                attempt, stop = _complete_attempt(
                    case,
                    run_number=run_number,
                    model=authorization.provider.model_id,
                    elapsed=elapsed,
                    usage=usage,
                    safe_payload=safe_payload,
                )
                attempts.append(attempt)
                if stop:
                    hard_stops.append(stop)
                else:
                    results.append(result)
            except Exception as exc:
                usage = consume_provider_context_metadata()
                provider_attempt_count = _optional_usage(
                    usage, "provider_attempt_count"
                )
                if provider_attempt_count is not None and provider_attempt_count > 0:
                    manifest["first_data_request_sent"] = True
                stop = _usage_stop(usage, authorization.provider.model_id)
                if stop is None:
                    stop = "REPEATED_PROVIDER_FAILURE"
                hard_stops.append(stop)
                attempts.append(
                    SafeReportProviderAttempt(
                        case_id=case.case_id,
                        partition=case.partition,
                        run_number=run_number,
                        response_sha256=_canonical_sha256(
                            {"error_type": type(exc).__name__, "stop": stop}
                        ),
                        provider_model=str(
                            usage.get("provider_model")
                            or authorization.provider.model_id
                        ),
                        provider_attempts=_optional_usage(
                            usage, "provider_attempt_count"
                        ),
                        provider_metered_attempts=_optional_usage(
                            usage, "provider_metered_attempt_count"
                        ),
                        retry_count=_retry_count(usage),
                        input_tokens=_optional_usage(usage, "provider_input_tokens"),
                        output_tokens=_optional_usage(usage, "provider_output_tokens"),
                        cached_input_tokens=_optional_usage(
                            usage, "provider_cached_input_tokens"
                        ),
                        latency_seconds=None,
                        capture_status="hard_stopped",
                        stable_error_code=stop,
                    )
                )
            capture = _capture(
                attempts,
                run_id=run_id,
                dataset=dataset,
                dataset_sha256=dataset_sha256,
                authorization=authorization,
                discovery=discovery,
                candidate_revision=candidate_revision,
                candidate_tree=candidate_tree,
                hard_stops=hard_stops,
            )
            if (
                capture.outbound_requests_attempted is not None
                and capture.outbound_requests_metered is not None
                and (
                    capture.outbound_requests_attempted != durable_attempt_count
                    or capture.outbound_requests_metered != durable_attempt_count
                )
            ):
                hard_stops.append("EVIDENCE_PERSISTENCE_UNAVAILABLE")
                capture = _capture(
                    attempts,
                    run_id=run_id,
                    dataset=dataset,
                    dataset_sha256=dataset_sha256,
                    authorization=authorization,
                    discovery=discovery,
                    candidate_revision=candidate_revision,
                    candidate_tree=candidate_tree,
                    hard_stops=hard_stops,
                )
            _write_json_atomic(
                store.run_dir / "local-redacted-provider-capture.json",
                capture.model_dump(mode="json"),
            )
            manifest.update(_usage_manifest_fields(capture))
            store.write_manifest(manifest)
            if hard_stops:
                return capture, results
    return (
        _capture(
            attempts,
            run_id=run_id,
            dataset=dataset,
            dataset_sha256=dataset_sha256,
            authorization=authorization,
            discovery=discovery,
            candidate_revision=candidate_revision,
            candidate_tree=candidate_tree,
            hard_stops=hard_stops,
        ),
        results,
    )


def _complete_attempt(
    case: CalibrationCase,
    *,
    run_number: int,
    model: str,
    elapsed: float,
    usage: dict[str, Any],
    safe_payload: dict[str, Any],
) -> tuple[SafeReportProviderAttempt, str | None]:
    stop = _usage_stop(usage, model)
    attempted = _optional_usage(usage, "provider_attempt_count")
    metered = _optional_usage(usage, "provider_metered_attempt_count")
    if stop:
        return (
            SafeReportProviderAttempt(
                case_id=case.case_id,
                partition=case.partition,
                run_number=run_number,
                response_sha256=_canonical_sha256(safe_payload),
                provider_model=str(usage.get("provider_model") or model),
                provider_attempts=attempted,
                provider_metered_attempts=metered,
                retry_count=max(0, attempted - 1) if attempted is not None else None,
                input_tokens=_optional_usage(usage, "provider_input_tokens"),
                output_tokens=_optional_usage(usage, "provider_output_tokens"),
                cached_input_tokens=_optional_usage(
                    usage, "provider_cached_input_tokens"
                ),
                latency_seconds=elapsed,
                capture_status="hard_stopped",
                stable_error_code=stop,
                structured_payload=safe_payload,
            ),
            stop,
        )
    return (
        SafeReportProviderAttempt(
            case_id=case.case_id,
            partition=case.partition,
            run_number=run_number,
            response_sha256=_canonical_sha256(safe_payload),
            provider_model=model,
            provider_attempts=attempted,
            provider_metered_attempts=metered,
            retry_count=max(0, attempted - 1) if attempted is not None else None,
            input_tokens=int(usage["provider_input_tokens"]),
            output_tokens=int(usage["provider_output_tokens"]),
            cached_input_tokens=int(usage["provider_cached_input_tokens"]),
            latency_seconds=elapsed,
            capture_status="complete",
            structured_payload=safe_payload,
        ),
        None,
    )


def _usage_stop(usage: dict[str, Any], expected_model: str) -> str | None:
    attempted = _optional_usage(usage, "provider_attempt_count")
    metered = _optional_usage(usage, "provider_metered_attempt_count")
    if (
        attempted is None
        or metered is None
        or attempted < 1
        or attempted != metered
        or not usage.get("provider_usage_available")
        or any(
            key not in usage
            for key in (
                "provider_input_tokens",
                "provider_output_tokens",
                "provider_cached_input_tokens",
            )
        )
    ):
        return "USAGE_METERING_UNAVAILABLE"
    if usage.get("provider_model") != expected_model:
        return "PROVIDER_OR_MODEL_MISMATCH"
    if attempted is not None and attempted > 2:
        return "RETRY_AMPLIFICATION_EXCEEDED"
    return None


def _capture(
    attempts: Sequence[SafeReportProviderAttempt],
    *,
    run_id: str,
    dataset: CalibrationDataset,
    dataset_sha256: str,
    authorization,
    discovery,
    candidate_revision: str,
    candidate_tree: str,
    hard_stops: Sequence[str],
) -> SafeReportProviderCapture:
    return SafeReportProviderCapture(
        run_id=run_id,
        dataset_id=dataset.dataset_id,
        dataset_sha256=dataset_sha256,
        provider_name=authorization.provider.name,
        model_id=authorization.provider.model_id,
        candidate_revision=candidate_revision,
        candidate_tree=candidate_tree,
        capture_status="hard_stopped" if hard_stops else "complete",
        hard_stop_conditions=list(dict.fromkeys(hard_stops)),
        attempts=list(attempts),
        outbound_requests_attempted=_sum_optional(
            [item.provider_attempts for item in attempts]
        ),
        outbound_requests_metered=_sum_optional(
            [item.provider_metered_attempts for item in attempts]
        ),
        pricing_snapshot=discovery,
    )


def _case_to_input(case: CalibrationCase) -> tuple[InterviewPlan, list[dict[str, Any]]]:
    kind_map = {
        "technical": "technical",
        "system_design": "system-design",
        "project_review": "project",
        "behavioral": "behavioral",
    }
    question_kind = kind_map[case.question_type]
    plan = InterviewPlan(
        title=f"T65 report scoring: {case.group_id}",
        questions=[
            InterviewQuestion(
                id=case.case_id,
                kind=question_kind,
                prompt=case.question,
                focus=case.question,
            )
        ],
    )
    item: dict[str, Any] = {
        "question_id": case.case_id,
        "question_text": case.question,
        "question_kind": question_kind,
        "focus": case.question,
        "answer_state": "answered" if case.answer else "unanswered",
        "messages": [
            {
                "role": "candidate",
                "content": case.answer,
                "question_id": case.case_id,
            }
        ],
        "scoring_references": [],
        "answer_references": [],
    }
    item["applicable_dimensions"] = applicable_dimensions_for_item(item)
    return plan, [item]


def _report_attempt_result(
    case: CalibrationCase,
    report,
    evaluation_item: dict[str, Any],
    *,
    run_number: int,
) -> AttemptResult:
    feedback = report.feedbacks[0]
    observed = [
        value
        for evidence in feedback.dimension_evidence
        for value in evidence.get("observed", [])
    ]
    return AttemptResult(
        case_id=case.case_id,
        group_id=case.group_id,
        quality_level=case.quality_label,
        run_number=run_number,
        score=feedback.score,
        expected_score_range=case.expected_score_range,
        language=case.language,
        question_type=case.question_type,
        answer=case.answer,
        observed=observed,
        required_observations=list(case.required_evidence),
        forbidden_claims=list(case.forbidden_claims),
        applicable_dimensions=list(feedback.applicable_dimensions),
        expected_applicable_dimensions=list(evaluation_item["applicable_dimensions"]),
        fallback=report.is_fallback,
        output_text=" ".join(
            [feedback.rationale, feedback.critique, feedback.better_answer]
        ),
    )


def _safe_scoring_result(
    result: AttemptResult,
    *,
    ngram_min_coverage: float | None = None,
) -> dict[str, Any]:
    if ngram_min_coverage is None:
        ngram_min_coverage = load_gate_config().algorithm_parameters[
            "evidence_ngram_min_coverage"
        ]
    normalized_answer = normalize_text(result.answer)
    normalized_terms = [
        normalize_text(value)
        for value in result.required_observations
        if normalize_text(value)
    ]
    grounding_hits = []
    for evidence in result.observed:
        normalized_evidence = normalize_text(evidence)
        grounding_hits.append(
            ngram_coverage(evidence, result.answer)
            >= ngram_min_coverage
            or any(
                term in normalized_evidence and term in normalized_answer
                for term in normalized_terms
            )
        )
    normalized_output = normalize_text(
        " ".join([*result.observed, result.output_text])
    )
    forbidden_claim_hits = [
        bool(
            normalize_text(claim)
            and normalize_text(claim) in normalized_output
            and normalize_text(claim) not in normalized_answer
        )
        for claim in result.forbidden_claims
    ]
    return {
        "case_id": result.case_id,
        "run_number": result.run_number,
        "score": result.score,
        "observed_count": len(result.observed),
        "grounding_hits": grounding_hits,
        "forbidden_claim_hits": forbidden_claim_hits,
        "applicable_dimensions": result.applicable_dimensions,
        "fallback": result.fallback,
        "output_sha256": hashlib.sha256(result.output_text.encode("utf-8")).hexdigest(),
    }


def _replay_attempts(
    capture: SafeReportProviderCapture,
    selected: Sequence[CalibrationCase],
) -> list[AttemptResult]:
    by_id = {case.case_id: case for case in selected}
    results: list[AttemptResult] = []
    for attempt in capture.attempts:
        if attempt.case_id not in by_id or attempt.capture_status != "complete":
            continue
        payload = attempt.structured_payload or {}
        scoring = payload.get("scoring_result")
        if not isinstance(scoring, dict):
            raise ValueError("saved replay is missing safe scoring_result")
        case = by_id[attempt.case_id]
        _, evaluation_items = _case_to_input(case)
        grounding_hits = scoring.get("grounding_hits")
        forbidden_claim_hits = scoring.get("forbidden_claim_hits")
        if (
            not isinstance(grounding_hits, list)
            or any(not isinstance(value, bool) for value in grounding_hits)
            or scoring.get("observed_count") != len(grounding_hits)
            or not isinstance(forbidden_claim_hits, list)
            or any(not isinstance(value, bool) for value in forbidden_claim_hits)
            or len(forbidden_claim_hits) != len(case.forbidden_claims)
        ):
            raise ValueError("saved replay is missing redacted scoring derivations")
        results.append(
            AttemptResult(
                case_id=case.case_id,
                group_id=case.group_id,
                quality_level=case.quality_label,
                run_number=attempt.run_number,
                score=scoring.get("score"),
                expected_score_range=case.expected_score_range,
                language=case.language,
                question_type=case.question_type,
                answer=case.answer,
                observed=[case.answer if hit else "" for hit in grounding_hits],
                required_observations=list(case.required_evidence),
                forbidden_claims=list(case.forbidden_claims),
                applicable_dimensions=scoring.get("applicable_dimensions", []),
                expected_applicable_dimensions=evaluation_items[0][
                    "applicable_dimensions"
                ],
                fallback=bool(scoring.get("fallback", False)),
                output_text=" ".join(
                    claim
                    for claim, hit in zip(
                        case.forbidden_claims, forbidden_claim_hits
                    )
                    if hit
                ),
            )
        )
    return results


def _validate_replay_capture(
    capture: SafeReportProviderCapture,
    *,
    dataset: CalibrationDataset,
    authorization_model: str,
    candidate_revision: str,
    candidate_tree: str,
) -> None:
    if capture.dataset_id != dataset.dataset_id:
        raise ValueError("saved replay dataset mismatch")
    if capture.model_id != authorization_model:
        raise ValueError("saved replay model mismatch")
    if (
        capture.candidate_revision != candidate_revision
        or capture.candidate_tree != candidate_tree
    ):
        raise ValueError("saved replay candidate mismatch")


def _usage_manifest_fields(capture: SafeReportProviderCapture) -> dict[str, Any]:
    attempts = list(capture.attempts)
    complete_usage = all(
        item.input_tokens is not None
        and item.output_tokens is not None
        and item.cached_input_tokens is not None
        for item in attempts
    )
    model_price = capture.pricing_snapshot.prices.get(capture.model_id)
    input_tokens = (
        sum(int(item.input_tokens) for item in attempts) if complete_usage else None
    )
    output_tokens = (
        sum(int(item.output_tokens) for item in attempts) if complete_usage else None
    )
    cached_tokens = (
        sum(int(item.cached_input_tokens) for item in attempts)
        if complete_usage
        else None
    )
    cost = None
    if (
        model_price is not None
        and input_tokens is not None
        and output_tokens is not None
        and cached_tokens is not None
    ):
        cost = estimate_provider_cost(
            price=model_price,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_input_tokens=cached_tokens,
        )
    return {
        "updated_at": _utc_now(),
        "provider_called": (
            capture.outbound_requests_attempted > 0
            if capture.outbound_requests_attempted is not None
            else None
        ),
        "inference_attempted": capture.outbound_requests_attempted,
        "inference_metered": capture.outbound_requests_metered,
        "retries": _sum_optional([item.retry_count for item in attempts]),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cached_input_tokens": cached_tokens,
        "estimated_cost": cost,
        "hard_stop_conditions": list(capture.hard_stop_conditions),
    }


def _replay_usage_manifest_fields(
    capture: SafeReportProviderCapture,
) -> dict[str, Any]:
    source = _usage_manifest_fields(capture)
    return {
        "provider_called": False,
        "provider_called_this_run": False,
        "inference_attempted": 0,
        "inference_metered": 0,
        "retries": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cached_input_tokens": 0,
        "estimated_cost": 0.0,
        "hard_stop_conditions": ["SOURCE_CAPTURE_INCOMPLETE"],
        "replayed_source_usage": source,
    }


def _mark_replay_diagnostic(manifest: dict[str, Any]) -> None:
    manifest.update(
        {
            "decision": "BLOCKED_DIAGNOSTIC_ONLY",
            "quality_status": "BLOCKED_DIAGNOSTIC_ONLY",
            "formal_evidence_eligible": False,
            "hard_stop_conditions": ["SOURCE_CAPTURE_INCOMPLETE"],
        }
    )


def _mark_replay_metrics_diagnostic(metrics: dict[str, Any]) -> None:
    metrics.update(
        {
            "diagnostic_computed_decision": metrics.get("decision"),
            "diagnostic_computed_passed": metrics.get("passed"),
            "decision": "BLOCKED_DIAGNOSTIC_ONLY",
            "quality_status": "BLOCKED_DIAGNOSTIC_ONLY",
            "formal_evidence_eligible": False,
            "passed": False,
        }
    )


def _finish_blocked(
    store: EvaluationArtifactStore,
    manifest: dict[str, Any],
    stops: Sequence[str],
) -> int:
    manifest.update(
        {
            "updated_at": _utc_now(),
            "decision": "BLOCKED",
            "quality_status": "BLOCKED",
            "hard_stop_conditions": list(dict.fromkeys(stops)),
        }
    )
    store.write_manifest(manifest)
    print(_status_json("BLOCKED", manifest["hard_stop_conditions"][0], manifest["run_id"]))
    return 2


def _candidate_identity() -> tuple[str, str, bool]:
    revision = _git("rev-parse", "HEAD")
    tree = _git("show", "-s", "--format=%T", "HEAD")
    clean = not _git("status", "--porcelain")
    return revision, tree, clean


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.strip()


def _blind_partition_released(execution_manifest: dict[str, Any]) -> bool:
    return (
        execution_manifest.get("task_status", {}).get("T26") == "PASS"
        and execution_manifest.get("t26", {}).get("review_status") == "PASS"
    )


def _prompt_sha256() -> str:
    source = inspect.getsource(OpenAIInterviewLLM._build_report_prompt)
    return hashlib.sha256(
        f"{REPORT_EVIDENCE_PROMPT_VERSION}\n{source}".encode("utf-8")
    ).hexdigest()


def _append_attempt_start(
    store: EvaluationArtifactStore,
    *,
    case_id: str,
    run_number: int,
    attempt_number: int,
    authorization_id: str,
    authorization_sha256: str,
    provider: str,
    model: str,
    candidate_revision: str,
    candidate_tree: str,
) -> None:
    path = store.run_dir / "attempt-start-ledger.jsonl"
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(
            json.dumps(
                {
                    "case_id": case_id,
                    "run_number": run_number,
                    "attempt_number": attempt_number,
                    "authorization_id": authorization_id,
                    "authorization_sha256": authorization_sha256,
                    "provider": provider,
                    "model": model,
                    "candidate_revision": candidate_revision,
                    "candidate_tree": candidate_tree,
                    "started_at": _utc_now(),
                    "status": "ATTEMPT_START_PERSISTED",
                },
                sort_keys=True,
            )
            + "\n"
        )
        stream.flush()
        os.fsync(stream.fileno())


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    temporary.replace(path)


def _canonical_sha256(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _optional_usage(usage: dict[str, Any], key: str) -> int | None:
    value = usage.get(key)
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


def _retry_count(usage: dict[str, Any]) -> int | None:
    attempted = _optional_usage(usage, "provider_attempt_count")
    return max(0, attempted - 1) if attempted is not None else None


def _sum_optional(values: Sequence[int | None]) -> int | None:
    if any(value is None for value in values):
        return None
    return sum(value for value in values if value is not None)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _default_run_id() -> str:
    return datetime.now(timezone.utc).strftime("t65-report-%Y%m%dT%H%M%SZ")


def _status_json(status: str, detail: str, run_id: str | None = None) -> str:
    return json.dumps(
        {
            "schema_version": "t65-report-scoring-cli-status-v1",
            "status": status,
            "detail": detail,
            "run_id": run_id,
        },
        sort_keys=True,
    )


if __name__ == "__main__":
    raise SystemExit(main())
