from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.decision_store import InMemoryDecisionStore
from app.services.followup_decision_service import (
    DecisionProviderResult,
    FollowupDecisionExecutionService,
)
from app.services.followup_diagnostics import (
    FollowupDiagnosticInput,
    is_duplicate_followup_text,
)
from app.services.followup_eval import (
    SavedDecisionAttempt,
    SavedFollowupCaseResponse,
    SavedFollowupProviderArtifact,
    SavedGenerationAttempt,
    build_synthetic_fixture_replay,
    calculate_followup_metrics,
    fixed_policy_attempts,
    load_saved_provider_artifact,
    replay_saved_provider_artifact,
)
from app.services.followup_prompts import (
    FOLLOWUP_DECISION_PROMPT_SHA256,
    FOLLOWUP_DECISION_PROMPT_VERSION,
    FOLLOWUP_GENERATION_PROMPT_SHA256,
    FOLLOWUP_GENERATION_PROMPT_VERSION,
    StructuredFollowupDecisionProvider,
    StructuredFollowupGenerationProvider,
    generation_context_for_decision,
)
from app.services.followup_provider_preflight import (
    discover_deepseek_provider,
    estimate_provider_cost,
    evaluate_followup_provider_preflight,
)
from app.services.interview_quality_dataset import (
    InterviewQualityCase,
    InterviewQualityDataset,
    load_interview_quality_dataset,
)
from app.services.interview_quality_gate import load_gate_config
from app.services.interview_quality_provider_authorization import (
    load_provider_authorization,
)
from app.services.llm import LLMConfig, OpenAIInterviewLLM
from app.services.report_eval_artifacts import EvaluationArtifactStore


DEFAULT_DATASET = Path(
    "tests/golden/interview_quality_v1/followup-decision-quality-v2.json"
)
DEFAULT_GATE = Path("config/interview_quality_v1_gate.json")
DEFAULT_AUTHORIZATION = Path(
    "config/interview_quality_v1_provider_authorization.json"
)
DEFAULT_DATASET_MANIFEST = Path("tests/golden/interview_quality_v1/manifest.json")
DEFAULT_EXECUTION_MANIFEST = Path(
    "docs/interview-quality-v1-execution-manifest.json"
)
DEFAULT_OUT = Path("tmp/interview-quality-v1-provider-runs")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evaluate fixed_v1 versus adaptive_v1 follow-up quality"
    )
    parser.add_argument(
        "--mode",
        choices=("fixture-replay", "saved-replay", "provider"),
        default="fixture-replay",
    )
    parser.add_argument("--scope", choices=("smoke", "full"), default="full")
    parser.add_argument(
        "--purpose", choices=("development", "evaluation"), default="evaluation"
    )
    parser.add_argument(
        "--partition", choices=("train", "dev", "blind-test", "all"), default="all"
    )
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--gate-config", type=Path, default=DEFAULT_GATE)
    parser.add_argument("--authorization", type=Path, default=DEFAULT_AUTHORIZATION)
    parser.add_argument(
        "--dataset-manifest", type=Path, default=DEFAULT_DATASET_MANIFEST
    )
    parser.add_argument(
        "--execution-manifest", type=Path, default=DEFAULT_EXECUTION_MANIFEST
    )
    parser.add_argument("--responses", type=Path)
    parser.add_argument("--case-id", action="append", default=[])
    parser.add_argument("--smoke-case-count", type=int, default=8)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--run-id")
    parser.add_argument("--request-timeout-seconds", type=float, default=60)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.purpose == "development" and args.partition in {"blind-test", "all"}:
        raise SystemExit(
            "development runs cannot consume blind-test; select train or dev"
        )
    if args.mode == "saved-replay" and args.responses is None:
        raise SystemExit("--responses is required for saved-replay")
    if args.mode != "saved-replay" and args.responses is not None:
        raise SystemExit("--responses is only valid for saved-replay")
    if args.smoke_case_count <= 0:
        raise SystemExit("--smoke-case-count must be positive")

    dataset_path = args.dataset.resolve()
    gate_path = args.gate_config.resolve()
    authorization_path = args.authorization.resolve()
    dataset_manifest_path = args.dataset_manifest.resolve()
    execution_manifest_path = args.execution_manifest.resolve()
    full_dataset = load_interview_quality_dataset(dataset_path)
    selected = _select_dataset(
        full_dataset,
        partition=args.partition,
        case_ids=args.case_id,
        scope=args.scope,
        smoke_case_count=args.smoke_case_count,
    )
    dataset_sha256 = _sha256_file(dataset_path)
    gate_config = load_gate_config(gate_path)
    authorization = load_provider_authorization(authorization_path)
    run_id = args.run_id or datetime.now(timezone.utc).strftime(
        "followup-t36-%Y%m%dT%H%M%SZ"
    )
    run_dir = args.out.resolve() / run_id
    if run_dir.exists() and any(run_dir.iterdir()):
        raise SystemExit(
            f"run directory already exists; choose a new --run-id: {run_dir}"
        )
    store = EvaluationArtifactStore.create(
        root=args.out.resolve(),
        run_id=run_id,
        manifest={
            "schema_version": "followup-quality-run-v1",
            "run_id": run_id,
            "created_at": _utc_now(),
            "task": "T36",
            "mode": args.mode,
            "scope": args.scope,
            "purpose": args.purpose,
            "partition": args.partition,
            "dataset_id": full_dataset.dataset_id,
            "dataset_sha256": dataset_sha256,
            "selected_case_count": len(selected.cases),
            "selected_case_ids": [case.case_id for case in selected.cases],
            "gate_config_sha256": _sha256_file(gate_path),
            "authorization_id": authorization.authorization_id,
            "authorization_sha256": _sha256_file(authorization_path),
            "provider": authorization.provider.name,
            "model": authorization.provider.model_id,
            "base_url": authorization.provider.base_url,
            "decision_prompt_version": FOLLOWUP_DECISION_PROMPT_VERSION,
            "decision_prompt_sha256": FOLLOWUP_DECISION_PROMPT_SHA256,
            "generation_prompt_version": FOLLOWUP_GENERATION_PROMPT_VERSION,
            "generation_prompt_sha256": FOLLOWUP_GENERATION_PROMPT_SHA256,
            "provider_called": False,
            "hard_stop_conditions": [],
            "decision": "RUNNING",
        },
    )
    manifest = store.read_manifest()

    if args.mode == "fixture-replay":
        artifact = build_synthetic_fixture_replay(
            selected,
            dataset_sha256=dataset_sha256,
        )
        _write_json_atomic(
            store.run_dir / "synthetic-fixture-replay.json",
            artifact.model_dump(mode="json"),
        )
        preflight = None
    elif args.mode == "saved-replay":
        artifact = load_saved_provider_artifact(args.responses.resolve())
        _write_json_atomic(
            store.run_dir / "normalized-saved-replay.json",
            artifact.model_dump(mode="json"),
        )
        preflight = None
        if artifact.source == "local_redacted_provider_output" and (
            artifact.provider_name != authorization.provider.name
            or artifact.model_id != authorization.provider.model_id
        ):
            return _finish_blocked(
                store,
                manifest,
                hard_stops=["PROVIDER_OR_MODEL_MISMATCH"],
                detail="saved response Provider identity is outside authorization",
            )
    else:
        key = os.getenv("DEEPSEEK_API_KEY") or os.getenv("OPENAI_API_KEY")
        discovery = discover_deepseek_provider(
            api_key=key,
            timeout_seconds=args.request_timeout_seconds,
        )
        preflight = evaluate_followup_provider_preflight(
            manifest=authorization,
            dataset=selected,
            dataset_path=dataset_path,
            gate_config_path=gate_path,
            authorization_path=authorization_path,
            dataset_file_manifest_path=dataset_manifest_path,
            execution_manifest_path=execution_manifest_path,
            discovery=discovery,
            credential_present=bool(key),
            evidence_persistence_available=_artifact_store_is_writable(store),
            environment_model=os.getenv("OPENAI_MODEL"),
        )
        manifest["provider_preflight"] = preflight.model_dump(mode="json")
        store.write_manifest(manifest)
        if not preflight.allowed:
            return _finish_blocked(
                store,
                manifest,
                hard_stops=preflight.hard_stop_conditions,
                detail="real Provider preflight stopped before the first data request",
            )
        assert key is not None
        artifact, live_stops = _record_live_provider_responses(
            selected,
            dataset_sha256=dataset_sha256,
            authorization=authorization,
            api_key=key,
            timeout_seconds=args.request_timeout_seconds,
        )
        _write_json_atomic(
            store.run_dir / "saved-provider-replay.json",
            artifact.model_dump(mode="json"),
        )
        manifest["provider_called"] = bool(
            any(
                case.decision_attempts or case.generation_attempts
                for case in artifact.cases
            )
        )
        if live_stops:
            manifest["completed_provider_case_count"] = len(artifact.cases)
            return _finish_blocked(
                store,
                manifest,
                hard_stops=live_stops,
                detail="real Provider run stopped before the next request",
            )

    adaptive = replay_saved_provider_artifact(
        selected,
        artifact,
        dataset_sha256=dataset_sha256,
    )
    fixed = fixed_policy_attempts(selected)
    metrics = calculate_followup_metrics(
        selected,
        [*fixed, *adaptive],
        gate_config=gate_config,
    )
    for attempt in [*fixed, *adaptive]:
        store.write_attempt(
            attempt.case_id,
            1 if attempt.policy_version == "fixed_v1" else 2,
            normalized=attempt.model_dump(mode="json"),
        )

    if args.mode == "provider" and preflight is not None:
        price = preflight.discovery.prices[authorization.provider.model_id]
        metrics["estimated_cost"] = estimate_provider_cost(
            price=price,
            input_tokens=metrics["input_tokens"],
            output_tokens=metrics["output_tokens"],
            cached_input_tokens=metrics["cached_input_tokens"],
        )
        metrics["pricing_source"] = preflight.discovery.pricing_source_url
        metrics["pricing_observed_at"] = preflight.discovery.observed_at
        metrics["pricing_currency"] = price.currency
    else:
        metrics["estimated_cost"] = None
        metrics["pricing_source"] = None
        metrics["pricing_observed_at"] = None
        metrics["pricing_currency"] = None

    store.write_metrics(metrics)
    store.write_report(render_report(metrics, mode=args.mode, scope=args.scope))
    manifest.update(
        {
            "updated_at": _utc_now(),
            "completed_case_count": len(adaptive),
            "provider_invocations_this_run": (
                metrics["provider_invocations"] if args.mode == "provider" else 0
            ),
            "recorded_or_simulated_provider_invocations": metrics[
                "provider_invocations"
            ],
            "recorded_or_simulated_provider_retries": metrics[
                "provider_retries"
            ],
            "input_tokens": metrics["input_tokens"],
            "output_tokens": metrics["output_tokens"],
            "cached_input_tokens": metrics["cached_input_tokens"],
            "decision_latency_seconds": metrics["decision_latency_seconds"],
            "generation_complete_latency_seconds": metrics[
                "generation_complete_latency_seconds"
            ],
            "total_latency_seconds": metrics["total_latency_seconds"],
            "automated_status": metrics["automated_status"],
            "quality_status": metrics["quality_status"],
            "decision": metrics["quality_status"],
        }
    )
    store.write_manifest(manifest)
    print(f"run_id={run_id}")
    print(f"run_dir={store.run_dir}")
    print(f"automated_status={metrics['automated_status']}")
    print(f"quality_status={metrics['quality_status']}")
    if metrics["automated_status"] == "FAIL":
        return 1
    if metrics["quality_status"] != "PASS":
        return 2
    return 0


class _RecordingDecisionProvider:
    def __init__(self, inner) -> None:
        self.inner = inner
        self.attempts: list[SavedDecisionAttempt] = []

    @property
    def prompt_version(self):
        return self.inner.prompt_version

    @property
    def prompt_sha256(self):
        return self.inner.prompt_sha256

    def __call__(self, context: dict[str, object]):
        started = time.perf_counter()
        try:
            result: DecisionProviderResult = self.inner(context)
        except TimeoutError:
            self.attempts.append(
                SavedDecisionAttempt(
                    kind="timeout",
                    latency_seconds=time.perf_counter() - started,
                )
            )
            raise
        except (ValidationError, ValueError, TypeError) as exc:
            self.attempts.append(
                SavedDecisionAttempt(
                    kind="invalid_output",
                    input_tokens=getattr(exc, "input_tokens", None),
                    output_tokens=getattr(exc, "output_tokens", None),
                    cached_input_tokens=getattr(exc, "cached_input_tokens", None),
                    provider_model=getattr(exc, "provider_model", None),
                    provider_response_id=getattr(exc, "provider_response_id", None),
                    latency_seconds=time.perf_counter() - started,
                )
            )
            raise
        except Exception:
            self.attempts.append(
                SavedDecisionAttempt(
                    kind="failure",
                    latency_seconds=time.perf_counter() - started,
                )
            )
            raise
        self.attempts.append(
            SavedDecisionAttempt(
                kind="success",
                payload=result.decision.model_dump(mode="json"),
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                cached_input_tokens=result.cached_input_tokens,
                provider_model=result.provider_model,
                provider_response_id=result.provider_response_id,
                latency_seconds=time.perf_counter() - started,
            )
        )
        return result


def _record_live_provider_responses(
    dataset: InterviewQualityDataset,
    *,
    dataset_sha256: str,
    authorization,
    api_key: str,
    timeout_seconds: float,
) -> tuple[SavedFollowupProviderArtifact, list[str]]:
    # Deliberately construct the frozen authorization identity. Environment
    # OPENAI_MODEL is never consumed by this path.
    config = LLMConfig(
        api_key=api_key,
        model=authorization.provider.model_id,
        base_url=authorization.provider.base_url,
        temperature=0,
        request_timeout_seconds=timeout_seconds,
        max_retries=0,
    )
    chat_model = OpenAIInterviewLLM._build_chat_model(config)
    decision_inner = StructuredFollowupDecisionProvider(chat_model, max_tokens=300)
    generation_provider = StructuredFollowupGenerationProvider(
        chat_model,
        max_tokens=120,
    )
    saved_cases: list[SavedFollowupCaseResponse] = []
    hard_stops: list[str] = []
    for case in dataset.cases:
        recording = _RecordingDecisionProvider(decision_inner)
        service = FollowupDecisionExecutionService(
            # A transport/parse exception without token usage is a metering
            # hard stop. Keep live evaluation to one Decision attempt; retry
            # behavior is exercised by saved-output replay without risking an
            # unmetered second outbound request.
            store=InMemoryDecisionStore(max_attempts=1),
            provider=recording,
        )
        request = _diagnostic_request(case)
        result = service.execute(
            request,
            source_command_id=f"provider-eval:{case.case_id}",
            worker_id="provider-eval",
        )
        generation_attempts: list[SavedGenerationAttempt] = []
        if any(
            item.input_tokens is None or item.output_tokens is None
            for item in recording.attempts
        ):
            hard_stops.append("USAGE_METERING_UNAVAILABLE")
        if any(
            item.provider_model != authorization.provider.model_id
            for item in recording.attempts
            if item.input_tokens is not None and item.output_tokens is not None
        ):
            hard_stops.append("PROVIDER_OR_MODEL_MISMATCH")
        decision = result.decision
        if not hard_stops and decision is not None and decision.action == "follow_up":
            context = generation_context_for_decision(
                _generation_transcript(case),
                decision,
            )
            for _ in range(3):
                generation_started = time.perf_counter()
                try:
                    generation = generation_provider(context)
                except TimeoutError:
                    generation_attempts.append(
                        SavedGenerationAttempt(
                            kind="timeout",
                            latency_seconds=time.perf_counter() - generation_started,
                        )
                    )
                    hard_stops.append("USAGE_METERING_UNAVAILABLE")
                    break
                except Exception:
                    generation_attempts.append(
                        SavedGenerationAttempt(
                            kind="failure",
                            latency_seconds=time.perf_counter() - generation_started,
                        )
                    )
                    hard_stops.append("USAGE_METERING_UNAVAILABLE")
                    break
                generation_attempts.append(
                    SavedGenerationAttempt(
                        kind="success",
                        text=generation.text,
                        input_tokens=generation.input_tokens,
                        output_tokens=generation.output_tokens,
                        cached_input_tokens=generation.cached_input_tokens,
                        provider_model=generation.provider_model,
                        provider_response_id=generation.provider_response_id,
                        latency_seconds=time.perf_counter() - generation_started,
                    )
                )
                if generation.input_tokens is None or generation.output_tokens is None:
                    hard_stops.append("USAGE_METERING_UNAVAILABLE")
                    break
                if generation.provider_model != authorization.provider.model_id:
                    hard_stops.append("PROVIDER_OR_MODEL_MISMATCH")
                    break
                if not is_duplicate_followup_text(
                    generation.text,
                    [case.input["question_text"], *case.input["asked_followups"]],
                ):
                    break
        saved_cases.append(
            SavedFollowupCaseResponse(
                case_id=case.case_id,
                decision_attempts=recording.attempts,
                generation_attempts=generation_attempts,
            )
        )
        if hard_stops:
            break
    artifact = SavedFollowupProviderArtifact(
        source="local_redacted_provider_output",
        dataset_id=dataset.dataset_id,
        dataset_sha256=dataset_sha256,
        provider_name=authorization.provider.name,
        model_id=authorization.provider.model_id,
        capture_status="hard_stopped" if hard_stops else "complete",
        hard_stop_conditions=list(dict.fromkeys(hard_stops)),
        cases=saved_cases,
    )
    return artifact, list(dict.fromkeys(hard_stops))


def render_report(metrics: dict, *, mode: str, scope: str) -> str:
    lines = [
        f"# T36 Follow-up Quality Evaluation: {metrics['quality_status']}",
        "",
        f"- mode: `{mode}`",
        f"- scope: `{scope}`",
        f"- automated_status: `{metrics['automated_status']}`",
        f"- independent_review_status: `{metrics['independent_review_status']}`",
        f"- cases: {metrics['adaptive_attempt_count']}",
        f"- recorded/replayed Provider invocations: {metrics['provider_invocations']}",
        "",
        "| Metric | Status | Actual | Sample |",
        "| --- | --- | ---: | ---: |",
    ]
    for item in metrics["metric_evaluations"]:
        lines.append(
            f"| `{item['metric_key']}` | {item['status']} | "
            f"{item['actual']:.6g} | {item['sample_size']} |"
        )
    lines.extend(
        [
            "",
            "## Policy comparison",
            "",
            f"- fixed_v1 action accuracy: {metrics['fixed_action_accuracy']:.3f}",
            f"- adaptive_v1 action accuracy: {metrics['adaptive_action_accuracy']:.3f}",
            f"- sequence replay: {metrics['sequence_replay']['sequence_count']}",
            (
                "- terminal zero-call checks: "
                f"{metrics['sequence_replay']['terminal_zero_call_passes']}/"
                f"{metrics['sequence_replay']['terminal_zero_call_checks']}"
            ),
            "",
            "Automated metrics and independent review are separate axes. A fixture or",
            "saved-output replay does not substitute for the pending blind review or a",
            "successful authorized real-Provider run.",
            "",
        ]
    )
    return "\n".join(lines)


def _select_dataset(
    dataset: InterviewQualityDataset,
    *,
    partition: str,
    case_ids: list[str],
    scope: str,
    smoke_case_count: int,
) -> InterviewQualityDataset:
    cases = list(dataset.cases)
    if partition != "all":
        cases = [case for case in cases if case.partition == partition]
    if case_ids:
        requested = set(case_ids)
        cases = [case for case in cases if case.case_id in requested]
        missing = requested - {case.case_id for case in cases}
        if missing:
            raise SystemExit(f"unknown or filtered case IDs: {sorted(missing)}")
    if scope == "smoke" and not case_ids:
        cases = _smoke_cases(cases, limit=smoke_case_count)
    if not cases:
        raise SystemExit("dataset selection is empty")
    return dataset.model_copy(update={"cases": cases})


def _smoke_cases(
    cases: list[InterviewQualityCase],
    *,
    limit: int,
) -> list[InterviewQualityCase]:
    selected: list[InterviewQualityCase] = []
    desired = [
        "normal",
        "provider_timeout",
        "provider_invalid_output",
        "provider_failed",
        "low_confidence",
    ]
    for mode in desired:
        match = next(
            (
                case
                for case in cases
                if case.input["provider_fixture"]["mode"] == mode
                and case not in selected
            ),
            None,
        )
        if match is not None:
            selected.append(match)
        if len(selected) >= limit:
            return selected
    for case in cases:
        if case not in selected:
            selected.append(case)
        if len(selected) >= limit:
            break
    return selected


def _diagnostic_request(case: InterviewQualityCase) -> FollowupDiagnosticInput:
    allowed = set(FollowupDiagnosticInput.model_fields)
    payload = {key: value for key, value in case.input.items() if key in allowed}
    payload["session_id"] = f"provider-eval-{case.case_id}"
    return FollowupDiagnosticInput.model_validate(payload)


def _generation_transcript(case: InterviewQualityCase) -> list[dict[str, str]]:
    transcript: list[dict[str, str]] = [
        {"role": "interviewer", "content": str(case.input["question_text"])}
    ]
    answers = list(case.input["candidate_answers"])
    followups = list(case.input["asked_followups"])
    for index, answer in enumerate(answers):
        transcript.append({"role": "candidate", "content": str(answer)})
        if index < len(followups):
            transcript.append(
                {"role": "interviewer", "content": str(followups[index])}
            )
    return transcript


def _artifact_store_is_writable(store: EvaluationArtifactStore) -> bool:
    try:
        path = store.run_dir / ".persistence-preflight"
        path.write_text("ok", encoding="utf-8")
        path.unlink()
        return True
    except OSError:
        return False


def _finish_blocked(
    store: EvaluationArtifactStore,
    manifest: dict,
    *,
    hard_stops: list[str],
    detail: str,
) -> int:
    decision = "BLOCKED_" + (hard_stops[0] if hard_stops else "UNKNOWN")
    manifest.update(
        {
            "updated_at": _utc_now(),
            "hard_stop_conditions": list(dict.fromkeys(hard_stops)),
            "decision": decision,
            "quality_status": "BLOCKED",
            "stop_detail": detail,
        }
    )
    store.write_manifest(manifest)
    store.write_report(
        "\n".join(
            [
                f"# T36 Follow-up Quality Evaluation: {decision}",
                "",
                detail,
                "",
                "No further Provider request was made after the hard stop.",
                "",
                "Hard stops:",
                *[f"- `{item}`" for item in hard_stops],
                "",
            ]
        )
    )
    print(f"run_id={manifest['run_id']}")
    print(f"run_dir={store.run_dir}")
    print(f"decision={decision}")
    return 2


def _write_json_atomic(path: Path, payload: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


if __name__ == "__main__":
    raise SystemExit(main())
