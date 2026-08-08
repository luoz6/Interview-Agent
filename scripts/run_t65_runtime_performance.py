from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping
from uuid import uuid4

from pydantic import ValidationError


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.followup_performance import PerformancePricingSnapshot
from app.services.followup_provider_preflight import (
    DeepSeekDiscoverySnapshot,
    discover_deepseek_provider,
)
from app.services.interview_quality_gate import evaluate_metric, load_gate_config
from app.services.interview_quality_provider_authorization import (
    load_provider_authorization,
)
from app.services.report_eval_artifacts import resolve_evaluation_run_dir
from app.services.t65_runtime_performance import (
    CapturedTimingBoundaries,
    RuntimeEvidenceBuildResult,
    T65RuntimeCapturePlan,
    build_runtime_performance_evidence,
    validate_capture_plan,
)
from app.services.t65_formal_execution_receipt import validate_t65_formal_route


DEFAULT_GATE = ROOT / "config" / "interview_quality_v1_gate.json"
DEFAULT_AUTHORIZATION = (
    ROOT / "config" / "interview_quality_v1_provider_authorization.json"
)
DEFAULT_EXECUTION_MANIFEST = (
    ROOT / "docs" / "interview-quality-v1-execution-manifest.json"
)
DEFAULT_OUT = ROOT / "tmp" / "interview-quality-v1-provider-runs"
SAFE_PREFIX = re.compile(r"^test_t65perf_[0-9a-f]{12}$")
SAFE_ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
REQUIRED_SIGNAL_NAMES = (
    "decision_complete",
    "provider_first_item",
    "followup_first_visible",
    "generation_complete",
    "next_question_visible",
    "sse_resume",
    "report_complete",
)

CaptureExecutor = Callable[[dict[str, Any]], Mapping[str, Any] | RuntimeEvidenceBuildResult]
ProviderSender = Callable[[Mapping[str, Any]], Any]
IdentityResolver = Callable[[], tuple[str, str, bool]]
DiscoveryExecutor = Callable[..., DeepSeekDiscoverySnapshot]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the fail-closed T65 production-equivalent performance harness"
    )
    parser.add_argument("--capture-plan", type=Path, required=True)
    parser.add_argument("--gate-config", type=Path, default=DEFAULT_GATE)
    parser.add_argument("--authorization", type=Path, default=DEFAULT_AUTHORIZATION)
    parser.add_argument(
        "--execution-manifest", type=Path, default=DEFAULT_EXECUTION_MANIFEST
    )
    parser.add_argument("--context-window-tokens", type=int, required=True)
    parser.add_argument("--postgres-dsn-env", default="POSTGRES_DSN")
    parser.add_argument("--runtime-table-prefix", default="AUTO")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--run-id")
    parser.add_argument("--request-timeout-seconds", type=float, default=60)
    parser.add_argument("--scope", choices=("smoke", "full"), default="full")
    parser.add_argument(
        "--provider-mode", choices=("fixture", "provider"), default="fixture"
    )
    return parser


def main(
    argv: list[str] | None = None,
    *,
    capture_executor: CaptureExecutor | None = None,
    provider_sender: ProviderSender | None = None,
    discovery_executor: DiscoveryExecutor = discover_deepseek_provider,
    identity_resolver: IdentityResolver | None = None,
) -> int:
    args = build_parser().parse_args(argv)
    if args.context_window_tokens <= 0:
        raise SystemExit("--context-window-tokens must be positive")
    if args.request_timeout_seconds <= 0:
        raise SystemExit("--request-timeout-seconds must be positive")
    if SAFE_ENV_NAME.fullmatch(args.postgres_dsn_env) is None:
        raise SystemExit("--postgres-dsn-env must be an uppercase environment name")

    plan_path = args.capture_plan.resolve()
    gate_path = args.gate_config.resolve()
    authorization_path = args.authorization.resolve()
    execution_path = args.execution_manifest.resolve()
    raw_plan = _read_json(plan_path)
    source_spec = raw_plan.pop("source_capture", None)
    evidence_origin = (
        "saved_replay"
        if source_spec is not None
        else "injected_executor"
        if capture_executor is not None
        else "builtin_unavailable"
    )
    production_executor_sha256 = None
    formal_route_verified = validate_t65_formal_route(
        route="builtin_candidate", receipt=None
    )
    try:
        plan = T65RuntimeCapturePlan.model_validate(raw_plan)
        gate = load_gate_config(gate_path)
        validate_capture_plan(plan, gate)
        authorization = load_provider_authorization(authorization_path)
    except (OSError, ValueError, ValidationError) as exc:
        raise SystemExit(f"invalid frozen runtime inputs: {exc}") from exc

    run_id = args.run_id or datetime.now(timezone.utc).strftime(
        "t65-runtime-%Y%m%dT%H%M%SZ"
    )
    try:
        run_dir = resolve_evaluation_run_dir(args.out, run_id)
    except ValueError as exc:
        raise SystemExit(f"invalid --run-id: {exc}") from exc
    if run_dir.exists() and any(run_dir.iterdir()):
        raise SystemExit(f"run directory already exists: {run_dir}")
    run_dir.mkdir(parents=True, exist_ok=True)
    prefix = _resolve_prefix(args.runtime_table_prefix)
    postgres_dsn = os.getenv(args.postgres_dsn_env, "").strip()
    postgres_available = (
        _postgres_reachable(postgres_dsn)
        if args.provider_mode == "provider" and source_spec is None
        else False
    )
    manifest = {
        "schema_version": "t65-runtime-performance-run-v1",
        "task": "T65",
        "dimension": "latency_calls_cost",
        "run_id": run_id,
        "created_at": _utc_now(),
        "scope": args.scope,
        "provider_mode": args.provider_mode,
        "evidence_origin": evidence_origin,
        "production_executor_sha256": production_executor_sha256,
        "formal_evidence_eligible": False,
        "engineering_evidence_complete": False,
        "capture_plan_sha256": _sha256(plan_path),
        "gate_config_sha256": _sha256(gate_path),
        "authorization_id": authorization.authorization_id,
        "authorization_sha256": _sha256(authorization_path),
        "execution_manifest_sha256": _sha256(execution_path),
        "candidate_revision": plan.candidate_revision,
        "candidate_tree": plan.candidate_tree,
        "context_window_tokens": args.context_window_tokens,
        "postgres_dsn_env": args.postgres_dsn_env,
        "postgres_dsn_present": bool(postgres_dsn),
        "postgres_available": postgres_available,
        "runtime_table_prefix": prefix,
        "provider": authorization.provider.name,
        "model": authorization.provider.model_id,
        "provider_called": False,
        "provider_called_this_run": False,
        "first_data_request_sent": False,
        "hard_stop_conditions": [],
        "decision": "RUNNING",
    }
    _write_json(run_dir / "manifest.json", manifest)

    local_stops = _local_preflight_stops(
        plan=plan,
        plan_path=plan_path,
        gate_path=gate_path,
        authorization_path=authorization_path,
        execution_path=execution_path,
        context_window_tokens=args.context_window_tokens,
        provider_mode=args.provider_mode,
        source_spec=source_spec,
        capture_executor=capture_executor,
        provider_sender=provider_sender,
        identity_resolver=identity_resolver or _git_identity,
        postgres_available=postgres_available,
    )
    manifest["local_preflight"] = {
        "candidate_bound": "PROVIDER_CANDIDATE_MISMATCH" not in local_stops,
        "gate_config_bound": plan.gate_config_sha256 == _sha256(gate_path),
        "authorization_bound": plan.authorization_sha256
        == _sha256(authorization_path),
        "evidence_persistence_available": _writable(run_dir),
        "hard_stop_conditions": local_stops,
    }
    _write_json(run_dir / "manifest.json", manifest)

    if args.provider_mode == "fixture":
        return _finish_blocked(
            run_dir,
            manifest,
            stops=["BLOCKED_SYNTHETIC_FIXTURE_ONLY"],
            quality_status="BLOCKED_SYNTHETIC_FIXTURE_ONLY",
            detail="fixture mode is diagnostic and cannot be Provider Quality evidence",
        )
    if local_stops:
        return _finish_blocked(
            run_dir,
            manifest,
            stops=local_stops,
            detail="local preflight stopped before Provider discovery or case data",
        )

    key = os.getenv("DEEPSEEK_API_KEY") or os.getenv("OPENAI_API_KEY")
    discovery = discovery_executor(
        api_key=key,
        timeout_seconds=args.request_timeout_seconds,
    )
    discovery_stops = _discovery_stops(
        discovery=discovery,
        authorized_model=authorization.provider.model_id,
        credential_present=bool(key),
    )
    manifest["provider_preflight"] = {
        "credential_present": bool(key),
        "authorized_model": authorization.provider.model_id,
        "model_available": authorization.provider.model_id in discovery.model_ids,
        "pricing_available": authorization.provider.model_id in discovery.prices,
        "environment_model_ignored": bool(
            os.getenv("OPENAI_MODEL")
            and os.getenv("OPENAI_MODEL") != authorization.provider.model_id
        ),
        "discovery": discovery.model_dump(mode="json"),
        "hard_stop_conditions": discovery_stops,
    }
    _write_json(run_dir / "manifest.json", manifest)
    if discovery_stops:
        return _finish_blocked(
            run_dir,
            manifest,
            stops=discovery_stops,
            detail="Provider discovery stopped before the first data request",
        )

    transport = _FsyncAttemptTransport(
        run_dir=run_dir,
        manifest=manifest,
        provider=authorization.provider.name,
        model=authorization.provider.model_id,
        sender=provider_sender,
    )

    try:
        raw_capture = (
            _load_frozen_source_capture(plan_path, source_spec)
            if source_spec is not None
            else capture_executor(
                {
                    "plan": plan,
                    "gate_config": gate,
                    "authorization": authorization,
                    "discovery": discovery,
                    "run_dir": run_dir,
                    "runtime_table_prefix": prefix,
                    "postgres_dsn_env": args.postgres_dsn_env,
                    "scope": args.scope,
                    "request_timeout_seconds": args.request_timeout_seconds,
                    "context_window_tokens": args.context_window_tokens,
                    "send_provider_request": transport.send,
                }
            )
        )
    except Exception as exc:
        return _finish_blocked(
            run_dir,
            manifest,
            stops=["SOURCE_CAPTURE_INCOMPLETE"],
            detail=f"capture executor failed safely: {type(exc).__name__}",
        )

    if isinstance(raw_capture, RuntimeEvidenceBuildResult):
        result = raw_capture
        source_provider_calls = _result_provider_calls(result)
        report_completion = None
    else:
        try:
            capture_payload = dict(raw_capture)
            report_completion = capture_payload.get("report_completion")
            captures = [
                CapturedTimingBoundaries.model_validate(item)
                for item in capture_payload.get("captures", [])
            ]
            if not _complete_provider_usage_contract(captures):
                return _finish_blocked(
                    run_dir,
                    manifest,
                    stops=["USAGE_METERING_UNAVAILABLE"],
                    detail=(
                        "first-execution usage is incomplete or a normal follow-up "
                        "does not prove at least one Provider request"
                    ),
                )
            pricing = PerformancePricingSnapshot.model_validate(
                capture_payload["pricing_snapshot"]
            )
            source_sha = str(capture_payload["source_capture_sha256"])
            provider_name = str(capture_payload.get("provider_name", authorization.provider.name))
            model_id = str(capture_payload.get("model_id", authorization.provider.model_id))
            if provider_name != authorization.provider.name or model_id != authorization.provider.model_id:
                return _finish_blocked(
                    run_dir,
                    manifest,
                    stops=["PROVIDER_OR_MODEL_MISMATCH"],
                    detail="source capture identity is outside authorization",
                )
            result = build_runtime_performance_evidence(
                plan=plan,
                captures=captures,
                pricing=pricing,
                source_capture_sha256=source_sha,
                provider_name=provider_name,
                model_id=model_id,
                capture_run_id=str(capture_payload.get("capture_run_id", run_id)),
                gate_config=gate,
            )
            source_provider_calls = sum(item.provider_attempts for item in captures)
        except (KeyError, TypeError, ValueError, ValidationError) as exc:
            return _finish_blocked(
                run_dir,
                manifest,
                stops=["SOURCE_CAPTURE_INCOMPLETE"],
                detail=f"source capture failed validation: {type(exc).__name__}",
            )

    if source_spec is None and source_provider_calls != transport.attempt_count:
        return _finish_blocked(
            run_dir,
            manifest,
            stops=["EVIDENCE_PERSISTENCE_UNAVAILABLE"],
            detail=(
                "capture Provider attempts do not match fsync-persisted transport attempts"
            ),
        )
    manifest["source_provider_calls"] = source_provider_calls
    if result.status == "COMPLETE":
        report_gate, report_stop = _report_completion_gate(
            report_completion,
            plan=plan,
            gate=gate,
        )
        if report_stop is not None:
            _write_result_artifacts(run_dir, result)
            return _finish_blocked(
                run_dir,
                manifest,
                stops=[report_stop],
                detail=(
                    "report completion is not independently observable against "
                    "the frozen comparable baseline"
                ),
            )
        assert result.metrics is not None and report_gate is not None
        metrics = dict(result.metrics)
        metrics["gate_results"] = [
            *list(metrics.get("gate_results", [])),
            report_gate,
        ]
        if report_gate["status"] == "FAIL":
            metrics.update(
                {
                    "engineering_status": "FAIL",
                    "quality_status": "FAIL",
                    "overall_status": "FAIL",
                    "automated_gate_status": "FAIL",
                }
            )
        result = result.model_copy(update={"metrics": metrics})
    _write_result_artifacts(run_dir, result)

    if result.status != "COMPLETE":
        return _finish_blocked(
            run_dir,
            manifest,
            stops=list(result.hard_stop_conditions),
            quality_status=(
                result.observability.quality_status
                if result.observability is not None
                else "BLOCKED"
            ),
            detail="runtime capture is incomplete and cannot pass Quality",
            write_observability=False,
        )
    assert result.metrics is not None
    if args.scope == "smoke":
        return _finish_blocked(
            run_dir,
            manifest,
            stops=["SMOKE_COMPLETE_FULL_NOT_RUN"],
            quality_status="NOT_RUN_FULL_REQUIRED",
            detail="smoke completed; full frozen cohorts remain required",
            write_observability=False,
        )

    quality_status = str(result.metrics.get("quality_status", "BLOCKED"))
    overall_status = str(result.metrics.get("overall_status", "BLOCKED"))
    exit_code = _runtime_exit_contract(
        provider_mode=args.provider_mode,
        scope=args.scope,
        evidence_origin=evidence_origin,
        production_executor_sha256=production_executor_sha256,
        quality_status=quality_status,
        overall_status=overall_status,
    )
    if not formal_route_verified:
        exit_code = 2
    if not _formal_production_executor_eligible(
        evidence_origin=evidence_origin,
        production_executor_sha256=production_executor_sha256,
    ):
        return _finish_blocked(
            run_dir,
            manifest,
            stops=["PERFORMANCE_SIGNAL_NOT_OBSERVABLE"],
            quality_status="BLOCKED_DIAGNOSTIC_ONLY",
            detail=(
                "injected executors and saved replays are diagnostic only; "
                "no hash-bound built-in production-equivalent API/SSE executor exists"
            ),
        )
    engineering_evidence_complete = (
        evidence_origin == "builtin_production"
        and isinstance(production_executor_sha256, str)
        and SHA256_HEX.fullmatch(production_executor_sha256) is not None
        and quality_status == "PASS"
        and overall_status == "PASS"
    )
    manifest.update(
        {
            "updated_at": _utc_now(),
            "decision": overall_status,
            "quality_status": quality_status,
            "overall_status": overall_status,
            "hard_stop_conditions": [],
            "formal_evidence_eligible": formal_route_verified,
            "engineering_evidence_complete": engineering_evidence_complete,
        }
    )
    _write_json(run_dir / "manifest.json", manifest)
    return exit_code


def _runtime_exit_contract(
    *,
    provider_mode: str,
    scope: str,
    evidence_origin: str,
    production_executor_sha256: str | None,
    quality_status: str,
    overall_status: str,
) -> int:
    """Pure formal-exit contract, independent of capture execution mechanics."""
    formal_executor = _formal_production_executor_eligible(
        evidence_origin=evidence_origin,
        production_executor_sha256=production_executor_sha256,
    )
    if provider_mode != "provider" or scope != "full" or not formal_executor:
        return 2
    if quality_status == "PASS" and overall_status == "PASS":
        return 0
    if quality_status == "FAIL" or overall_status == "FAIL":
        return 1
    return 2


def _formal_production_executor_eligible(
    *,
    evidence_origin: str,
    production_executor_sha256: str | None,
) -> bool:
    return (
        evidence_origin == "builtin_production"
        and isinstance(production_executor_sha256, str)
        and SHA256_HEX.fullmatch(production_executor_sha256) is not None
    )


def _local_preflight_stops(
    *,
    plan: T65RuntimeCapturePlan,
    plan_path: Path,
    gate_path: Path,
    authorization_path: Path,
    execution_path: Path,
    context_window_tokens: int,
    provider_mode: str,
    source_spec: Any,
    capture_executor: CaptureExecutor | None,
    provider_sender: ProviderSender | None,
    identity_resolver: IdentityResolver,
    postgres_available: bool,
) -> list[str]:
    stops: list[str] = []
    if plan.gate_config_sha256 != _sha256(gate_path) or plan.authorization_sha256 != _sha256(authorization_path):
        stops.append("GATE_CONFIG_OR_DATASET_DRIFT")
    if context_window_tokens != 128_000:
        stops.append("CONTEXT_WINDOW_CAPABILITY_UNAVAILABLE")
    if not _frozen_report_baseline_valid(plan, plan_path):
        stops.append("GATE_CONFIG_OR_DATASET_DRIFT")
    execution = _read_json(execution_path)
    candidate = execution.get("t65_authorization_revalidation", {})
    if (
        candidate.get("provider_candidate_revision") != plan.candidate_revision
        or candidate.get("provider_candidate_tree") != plan.candidate_tree
    ):
        stops.append("PROVIDER_CANDIDATE_MISMATCH")
    try:
        revision, tree, clean = identity_resolver()
    except Exception:
        revision = tree = ""
        clean = False
    if revision != plan.candidate_revision or tree != plan.candidate_tree or not clean:
        stops.append("PROVIDER_CANDIDATE_MISMATCH")
    if provider_mode == "provider" and source_spec is None and capture_executor is None:
        stops.append("PERFORMANCE_SIGNAL_NOT_OBSERVABLE")
    if provider_mode == "provider" and source_spec is None and provider_sender is None:
        stops.append("PERFORMANCE_SIGNAL_NOT_OBSERVABLE")
    if provider_mode == "provider" and source_spec is not None:
        # A hash-bound replay can support diagnostics, but it cannot prove that
        # this formal candidate executed the production-equivalent path now.
        stops.append("SOURCE_CAPTURE_INCOMPLETE")
    if provider_mode == "provider" and source_spec is None and not postgres_available:
        stops.append("POSTGRES_UNAVAILABLE")
    return list(dict.fromkeys(stops))


class _FsyncAttemptTransport:
    """Persist ATTEMPT_START durably before delegating any Provider send."""

    def __init__(
        self,
        *,
        run_dir: Path,
        manifest: dict[str, Any],
        provider: str,
        model: str,
        sender: ProviderSender | None,
    ) -> None:
        self._ledger = run_dir / "provider-attempt-ledger.jsonl"
        self._manifest_path = run_dir / "manifest.json"
        self._manifest = manifest
        self._provider = provider
        self._model = model
        self._sender = sender
        self.attempt_count = 0

    def send(self, request: Mapping[str, Any]) -> Any:
        if self._sender is None:
            raise RuntimeError("controlled Provider sender is unavailable")
        if not isinstance(request, Mapping):
            raise TypeError("Provider request must be a mapping")
        next_attempt = self.attempt_count + 1
        entry = {
            "schema_version": "t65-provider-attempt-v1",
            "event": "ATTEMPT_START",
            "attempt_number": next_attempt,
            "attempt_id": uuid4().hex,
            "recorded_at": _utc_now(),
            "run_id": self._manifest["run_id"],
            "task": self._manifest["task"],
            "scope": self._manifest["scope"],
            "authorization_id": self._manifest["authorization_id"],
            "authorization_sha256": self._manifest["authorization_sha256"],
            "candidate_revision": self._manifest["candidate_revision"],
            "candidate_tree": self._manifest["candidate_tree"],
            "provider": self._provider,
            "model": self._model,
        }
        serialized = json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n"
        with self._ledger.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        self.attempt_count = next_attempt
        self._manifest.update(
            {
                "first_data_request_sent": True,
                "provider_called_this_run": True,
                "provider_called": True,
                "provider_attempt_starts": self.attempt_count,
                "last_attempt_start_at": entry["recorded_at"],
            }
        )
        _write_json(self._manifest_path, self._manifest)
        return self._sender(request)


def _complete_provider_usage_contract(
    captures: list[CapturedTimingBoundaries],
) -> bool:
    for capture in captures:
        usage = (
            capture.provider_attempts,
            capture.provider_metered_attempts,
            capture.retries,
            capture.fallback_count,
            capture.input_tokens,
            capture.output_tokens,
            capture.cached_input_tokens,
        )
        if capture.capture_complete and any(value is None for value in usage):
            return False
        cohort = capture.cohort
        normal_followup = (
            capture.capture_complete
            and cohort.first_or_recovery == "first"
            and cohort.followup_or_next_question == "follow_up"
            and capture.followup_count_before < 2
        )
        if normal_followup and (
            capture.provider_attempts is None or capture.provider_attempts < 1
        ):
            return False
    return True


def _frozen_report_baseline_valid(
    plan: T65RuntimeCapturePlan,
    plan_path: Path,
) -> bool:
    raw_path = plan.report_baseline_artifact
    expected_sha = plan.report_baseline_artifact_sha256
    if raw_path is None or expected_sha is None:
        return False
    relative = Path(raw_path)
    if relative.is_absolute() or ".." in relative.parts:
        return False
    plan_root = plan_path.parent.resolve()
    candidate = plan_root / relative
    current = plan_root
    try:
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                return False
    except OSError:
        return False
    baseline = candidate.resolve()
    try:
        baseline.relative_to(plan_root)
    except ValueError:
        return False
    try:
        if not baseline.is_file():
            return False
        return _sha256(baseline) == expected_sha
    except OSError:
        return False


def _discovery_stops(
    *,
    discovery: DeepSeekDiscoverySnapshot,
    authorized_model: str,
    credential_present: bool,
) -> list[str]:
    stops: list[str] = []
    if not credential_present or discovery.error_code == "credential":
        stops.append("CREDENTIAL_UNAVAILABLE")
    model_available = discovery.models_endpoint_ok and authorized_model in discovery.model_ids
    pricing_available = discovery.pricing_page_ok and authorized_model in discovery.prices
    if discovery.models_endpoint_ok and not model_available:
        stops.append("MODEL_VERSION_DRIFT")
    if model_available and not pricing_available:
        stops.append("USAGE_METERING_UNAVAILABLE")
    if discovery.error_code in {"network", "invalid_response"} and (
        discovery.model_request_attempts >= 3
        or discovery.pricing_request_attempts >= 3
    ):
        stops.append("REPEATED_PROVIDER_FAILURE")
    return list(dict.fromkeys(stops))


def _load_frozen_source_capture(plan_path: Path, source_spec: Any) -> dict[str, Any]:
    if not isinstance(source_spec, Mapping):
        raise ValueError("source_capture must be an object")
    raw_path = source_spec.get("path")
    expected_sha = source_spec.get("sha256")
    if not isinstance(raw_path, str) or not isinstance(expected_sha, str):
        raise ValueError("source_capture path and sha256 are required")
    relative = Path(raw_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("source capture must be a safe relative path")
    root = plan_path.parent.resolve()
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("source capture path cannot contain symlinks")
    path = (root / relative).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError("source capture escapes the capture-plan directory") from exc
    if not path.is_file():
        raise ValueError("source capture must be a regular file")
    if _sha256(path) != expected_sha:
        raise ValueError("source capture hash mismatch")
    payload = _read_json(path)
    payload.setdefault("source_capture_sha256", expected_sha)
    return payload


def _report_completion_gate(
    payload: Any,
    *,
    plan: T65RuntimeCapturePlan,
    gate: Any,
) -> tuple[dict[str, Any] | None, str | None]:
    """Evaluate report latency without treating follow-up timing as a proxy."""
    if not isinstance(payload, Mapping):
        return None, "PERFORMANCE_SIGNAL_NOT_OBSERVABLE"
    required = {
        "capture_complete",
        "comparable_baseline",
        "sample_count",
        "p95_seconds",
        "baseline_p95_seconds",
        "baseline_artifact_sha256",
    }
    if set(payload) != required:
        return None, "PERFORMANCE_SIGNAL_NOT_OBSERVABLE"
    if payload.get("capture_complete") is not True or payload.get("comparable_baseline") is not True:
        return None, "PERFORMANCE_SIGNAL_NOT_OBSERVABLE"
    if plan.report_baseline_artifact_sha256 is None:
        return None, "PERFORMANCE_SIGNAL_NOT_OBSERVABLE"
    if payload.get("baseline_artifact_sha256") != plan.report_baseline_artifact_sha256:
        return None, "GATE_CONFIG_OR_DATASET_DRIFT"
    sample_count = payload.get("sample_count")
    actual = payload.get("p95_seconds")
    baseline = payload.get("baseline_p95_seconds")
    if (
        isinstance(sample_count, bool)
        or not isinstance(sample_count, int)
        or sample_count < 0
        or isinstance(actual, bool)
        or not isinstance(actual, (int, float))
        or actual < 0
        or isinstance(baseline, bool)
        or not isinstance(baseline, (int, float))
        or baseline < 0
    ):
        return None, "PERFORMANCE_SIGNAL_NOT_OBSERVABLE"
    evaluated = evaluate_metric(
        gate,
        "operations.report_completion_p95_seconds",
        actual=float(actual),
        sample_size=sample_count,
        baseline=float(baseline),
    ).model_dump(mode="json")
    if evaluated["status"] in {"INSUFFICIENT_SAMPLE", "INSUFFICIENT_BASELINE"}:
        return None, "INSUFFICIENT_SAMPLE"
    return evaluated, None


def _write_result_artifacts(run_dir: Path, result: RuntimeEvidenceBuildResult) -> None:
    _write_json(run_dir / "result.json", result.model_dump(mode="json"))
    if result.performance_artifact is not None:
        _write_json(
            run_dir / "performance-artifact.json",
            result.performance_artifact.model_dump(mode="json"),
        )
    if result.metrics is not None:
        _write_json(run_dir / "metrics.json", result.metrics)
    if result.observability is not None:
        _write_json(
            run_dir / "observability.json",
            result.observability.model_dump(mode="json"),
        )


def _finish_blocked(
    run_dir: Path,
    manifest: dict[str, Any],
    *,
    stops: list[str],
    detail: str,
    quality_status: str = "BLOCKED",
    write_observability: bool = True,
) -> int:
    unique = list(dict.fromkeys(stops))
    manifest.update(
        {
            "updated_at": _utc_now(),
            "decision": "BLOCKED",
            "quality_status": quality_status,
            "overall_status": "BLOCKED",
            "hard_stop_conditions": unique,
            "detail": detail,
        }
    )
    _write_json(run_dir / "manifest.json", manifest)
    if write_observability:
        _write_json(
            run_dir / "observability.json",
            {
                "schema_version": "t65-runtime-cli-observability-v1",
                "quality_status": quality_status,
                "hard_stop_conditions": unique,
                "signals": [
                    {
                        "name": name,
                        "status": "not_observable",
                        "seconds": None,
                        "sample_count": 0,
                        "reason": detail,
                    }
                    for name in REQUIRED_SIGNAL_NAMES
                ],
            },
        )
    return 2


def _result_provider_calls(result: RuntimeEvidenceBuildResult) -> int:
    if result.performance_artifact is None:
        return 0
    return sum(item.actual_provider_requests for item in result.performance_artifact.samples)


def _resolve_prefix(value: str) -> str:
    resolved = f"test_t65perf_{uuid4().hex[:12]}" if value == "AUTO" else value
    if SAFE_PREFIX.fullmatch(resolved) is None:
        raise SystemExit(
            "--runtime-table-prefix must be AUTO or test_t65perf_<12 lowercase hex>"
        )
    return resolved


def _git_identity() -> tuple[str, str, bool]:
    revision = _git("rev-parse", "HEAD")
    tree = _git("rev-parse", "HEAD^{tree}")
    clean = not bool(_git("status", "--porcelain"))
    return revision, tree, clean


def _git(*args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _postgres_reachable(dsn: str) -> bool:
    if not dsn:
        return False
    try:
        import psycopg2

        with psycopg2.connect(
            dsn,
            connect_timeout=3,
            options="-c default_transaction_read_only=on",
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                return cursor.fetchone() == (1,)
    except Exception:
        return False


def _writable(run_dir: Path) -> bool:
    probe = run_dir / ".write-probe"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


if __name__ == "__main__":
    raise SystemExit(main())
