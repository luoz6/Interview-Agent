from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.followup_provider_preflight import (
    discover_deepseek_provider,
    estimate_provider_cost,
)
from app.services.initial_question_eval import (
    InitialQuestionEvalAttempt,
    InitialQuestionProviderArtifact,
    InitialQuestionReview,
    PlanContextBudgetEvidence,
    calculate_initial_question_metrics,
    fixture_artifact,
    load_initial_question_provider_artifact,
    render_initial_question_report,
    saved_replay_attempts,
)
from app.services.initial_question_provider_preflight import (
    evaluate_initial_question_provider_preflight,
)
from app.services.interview_plan_revision import (
    PlanConfigurationSnapshot,
    legacy_plan_to_v2,
    plan_payload_sha256,
)
from app.services.interview_quality_dataset import (
    InitialQuestionCaseInput,
    InterviewQualityDataset,
    load_interview_quality_dataset,
)
from app.services.interview_quality_gate import load_gate_config
from app.services.interview_quality_provider_authorization import (
    ProviderAuthorizationManifest,
    load_provider_authorization,
)
from app.services.job_tags import extract_job_tags
from app.services.llm import LLMConfig, OpenAIInterviewLLM
from app.services.prep import attach_prep_context
from app.services.provider_usage import (
    consume_provider_context_metadata,
    reset_provider_context_metadata,
)
from app.services.report_eval_artifacts import EvaluationArtifactStore


DEFAULT_DATASET = Path(
    "tests/golden/interview_quality_v1/initial-question-quality-v2.json"
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
        description="Evaluate configured initial interview-plan quality and budget"
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
    parser.add_argument("--dataset-manifest", type=Path, default=DEFAULT_DATASET_MANIFEST)
    parser.add_argument("--execution-manifest", type=Path, default=DEFAULT_EXECUTION_MANIFEST)
    parser.add_argument("--responses", type=Path)
    parser.add_argument("--case-id", action="append", default=[])
    parser.add_argument("--smoke-case-count", type=int, default=2)
    parser.add_argument("--context-window-tokens", type=int)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--run-id")
    parser.add_argument("--request-timeout-seconds", type=float, default=60)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.purpose == "development" and args.partition in {"blind-test", "all"}:
        raise SystemExit("development runs cannot consume blind-test")
    if args.mode == "saved-replay" and args.responses is None:
        raise SystemExit("--responses is required for saved-replay")
    if args.mode != "saved-replay" and args.responses is not None:
        raise SystemExit("--responses is only valid for saved-replay")
    if args.smoke_case_count <= 0:
        raise SystemExit("--smoke-case-count must be positive")
    if args.context_window_tokens is not None and args.context_window_tokens <= 0:
        raise SystemExit("--context-window-tokens must be positive")

    dataset_path = args.dataset.resolve()
    gate_path = args.gate_config.resolve()
    authorization_path = args.authorization.resolve()
    full_dataset = load_interview_quality_dataset(dataset_path)
    selected = _select_dataset(
        full_dataset,
        partition=args.partition,
        case_ids=args.case_id,
        scope=args.scope,
        smoke_case_count=args.smoke_case_count,
    )
    complete_frozen_dataset = (
        args.scope == "full"
        and args.partition == "all"
        and not args.case_id
        and len(selected.cases) == len(full_dataset.cases)
    )
    if args.mode == "fixture-replay" and not complete_frozen_dataset:
        raise SystemExit("fixture-replay evidence requires the full frozen dataset")
    dataset_sha256 = _sha256_file(dataset_path)
    gate_config = load_gate_config(gate_path)
    authorization = load_provider_authorization(authorization_path)
    run_id = args.run_id or datetime.now(timezone.utc).strftime(
        "initial-question-t57-%Y%m%dT%H%M%SZ"
    )
    run_dir = args.out.resolve() / run_id
    if run_dir.exists() and any(run_dir.iterdir()):
        raise SystemExit(f"run directory already exists: {run_dir}")
    store = EvaluationArtifactStore.create(
        root=args.out.resolve(),
        run_id=run_id,
        manifest={
            "schema_version": "initial-question-quality-run-v1",
            "run_id": run_id,
            "created_at": _utc_now(),
            "task": "T57",
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
            "provider_called": False,
            "first_data_request_sent": False,
            "hard_stop_conditions": [],
            "decision": "RUNNING",
        },
    )
    manifest = store.read_manifest()

    if args.mode == "fixture-replay":
        artifact = fixture_artifact(selected, dataset_sha256=dataset_sha256)
        _write_json(store.run_dir / "synthetic-fixture-replay.json", artifact.model_dump(mode="json"))
        attempts = list(artifact.attempts)
        provider_price = None
    elif args.mode == "saved-replay":
        artifact = load_initial_question_provider_artifact(
            args.responses.resolve(), dataset=full_dataset, dataset_path=dataset_path
        )
        if artifact.source == "local_redacted_provider_output" and (
            artifact.provider_name != authorization.provider.name
            or artifact.model_id != authorization.provider.model_id
        ):
            return _finish_blocked(
                store,
                manifest,
                ["PROVIDER_OR_MODEL_MISMATCH"],
                "saved response identity is outside the unified authorization",
            )
        _write_json(store.run_dir / "normalized-saved-replay.json", artifact.model_dump(mode="json"))
        selected_ids = {case.case_id for case in selected.cases}
        attempts = [
            item
            for item in saved_replay_attempts(artifact)
            if item.case_id in selected_ids
        ]
        provider_price = None
    else:
        key = os.getenv("DEEPSEEK_API_KEY") or os.getenv("OPENAI_API_KEY")
        discovery = discover_deepseek_provider(
            api_key=key,
            timeout_seconds=args.request_timeout_seconds,
        )
        preflight = evaluate_initial_question_provider_preflight(
            manifest=authorization,
            dataset=selected,
            dataset_path=dataset_path,
            gate_config_path=gate_path,
            authorization_path=authorization_path,
            dataset_file_manifest_path=args.dataset_manifest.resolve(),
            execution_manifest_path=args.execution_manifest.resolve(),
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
                list(preflight.hard_stop_conditions),
                "real Provider preflight stopped before the first data request",
            )
        if args.context_window_tokens is None:
            return _finish_blocked(
                store,
                manifest,
                ["GATE_CONFIG_OR_DATASET_DRIFT"],
                "authorized model needs an explicit frozen context-window capability",
            )
        assert key is not None
        artifact = _record_live_provider_responses(
            selected,
            dataset_sha256=dataset_sha256,
            authorization=authorization,
            api_key=key,
            timeout_seconds=args.request_timeout_seconds,
            context_window_tokens=args.context_window_tokens,
            smoke=args.scope == "smoke",
        )
        _write_json(store.run_dir / "saved-provider-replay.json", artifact.model_dump(mode="json"))
        manifest["provider_called"] = artifact.outbound_requests_attempted > 0
        manifest["first_data_request_sent"] = artifact.outbound_requests_attempted > 0
        if artifact.capture_status == "hard_stopped":
            return _finish_blocked(
                store,
                manifest,
                list(artifact.hard_stop_conditions),
                "real Provider run stopped before the next request",
            )
        if args.scope == "smoke":
            manifest.update(
                {
                    "updated_at": _utc_now(),
                    "completed_attempt_count": len(artifact.attempts),
                    "decision": "SMOKE_COMPLETE_FULL_NOT_RUN",
                }
            )
            store.write_manifest(manifest)
            print(f"run_id={run_id}")
            print("quality_status=NOT_RUN_FULL_REQUIRED")
            return 2
        attempts = list(artifact.attempts)
        provider_price = preflight.discovery.prices[authorization.provider.model_id]

    metrics = calculate_initial_question_metrics(selected, attempts, gate_config=gate_config)
    metrics["provider_usage"]["recorded_source_invocations"] = (
        artifact.outbound_requests_attempted
    )
    metrics["provider_usage"]["recorded_source_metered_invocations"] = (
        artifact.outbound_requests_metered
    )
    if not complete_frozen_dataset and metrics["quality_status"] == "PASS":
        metrics["quality_status"] = "BLOCKED_PARTIAL_DATASET"
    for attempt in attempts:
        store.write_attempt(
            attempt.case_id,
            attempt.run_number,
            normalized=attempt.model_dump(mode="json"),
        )
    if provider_price is not None:
        usage = metrics["provider_usage"]
        metrics["provider_usage"]["estimated_cost"] = estimate_provider_cost(
            price=provider_price,
            input_tokens=usage["input_tokens"],
            output_tokens=usage["output_tokens"],
            cached_input_tokens=usage["cached_input_tokens"],
        )
        metrics["provider_usage"]["pricing_currency"] = provider_price.currency
    else:
        metrics["provider_usage"]["estimated_cost"] = None
        metrics["provider_usage"]["pricing_currency"] = None
    store.write_metrics(metrics)
    store.write_report(render_initial_question_report(metrics))
    manifest.update(
        {
            "updated_at": _utc_now(),
            "completed_attempt_count": len(attempts),
            "completed_question_count": metrics["question_count"],
            "provider_invocations_this_run": metrics["provider_usage"]["provider_invocations_this_run"],
            "provider_metered_invocations": metrics["provider_usage"]["provider_metered_invocations"],
            "input_tokens": metrics["provider_usage"]["input_tokens"],
            "output_tokens": metrics["provider_usage"]["output_tokens"],
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
    return 0 if metrics["quality_status"] == "PASS" else 2


def _record_live_provider_responses(
    dataset: InterviewQualityDataset,
    *,
    dataset_sha256: str,
    authorization: ProviderAuthorizationManifest,
    api_key: str,
    timeout_seconds: float,
    context_window_tokens: int,
    smoke: bool,
) -> InitialQuestionProviderArtifact:
    llm = OpenAIInterviewLLM(
        LLMConfig(
            api_key=api_key,
            model=authorization.provider.model_id,
            base_url=authorization.provider.base_url,
            temperature=0.2,
            request_timeout_seconds=timeout_seconds,
            max_retries=0,
            context_window_tokens=context_window_tokens,
        )
    )
    attempts: list[InitialQuestionEvalAttempt] = []
    stops: list[str] = []
    outbound_requests_attempted = 0
    outbound_requests_metered = 0
    selected_cases = list(dataset.cases[:1] if smoke else dataset.cases)
    for case in selected_cases:
        item = InitialQuestionCaseInput.model_validate(case.input)
        configuration = PlanConfigurationSnapshot.model_validate(item.configuration)
        run_count = 1 if smoke else item.runs_per_case
        for run_number in range(1, run_count + 1):
            reset_provider_context_metadata()
            started = time.perf_counter()
            try:
                legacy = llm.generate_plan(
                    item.job_description,
                    item.resume_summary,
                    knowledge_context=item.knowledge_context,
                    configuration=configuration,
                )
            except Exception:
                metadata = consume_provider_context_metadata()
                outbound_requests_attempted += int(
                    metadata.get("provider_attempt_count", 0)
                )
                outbound_requests_metered += int(
                    metadata.get("provider_metered_attempt_count", 0)
                )
                stops.append(
                    "USAGE_METERING_UNAVAILABLE"
                    if metadata.get("provider_attempt_count")
                    != metadata.get("provider_metered_attempt_count", 0)
                    else "REPEATED_PROVIDER_FAILURE"
                )
                break
            latency = time.perf_counter() - started
            metadata = consume_provider_context_metadata()
            invocations = int(metadata.get("provider_attempt_count", 0))
            metered = int(metadata.get("provider_metered_attempt_count", 0))
            outbound_requests_attempted += invocations
            outbound_requests_metered += metered
            if not metadata.get("provider_usage_available") or metered != invocations:
                stops.append("USAGE_METERING_UNAVAILABLE")
                break
            provider_model = metadata.get("provider_model")
            if provider_model != authorization.provider.model_id:
                stops.append("PROVIDER_OR_MODEL_MISMATCH")
                break
            grounded = attach_prep_context(
                legacy,
                job_description=item.job_description,
                resume_text=item.resume_summary,
                job_tags=extract_job_tags(item.job_description),
            )
            plan = legacy_plan_to_v2(
                grounded,
                configuration_snapshot=configuration,
            )
            plan_sha256 = plan_payload_sha256(plan)
            attempts.append(
                InitialQuestionEvalAttempt(
                    case_id=case.case_id,
                    run_number=run_number,
                    partition=case.partition,
                    execution_source="live_provider",
                    plan=plan,
                    plan_sha256=plan_sha256,
                    session_snapshot_sha256=plan_sha256,
                    reviews=tuple(
                        InitialQuestionReview(
                            question_id=question.question_id,
                            review_status="pending",
                            reviewer_kind="unassigned",
                        )
                        for question in plan.questions
                    ),
                    context_budget=PlanContextBudgetEvidence(
                        evidence_source="runtime_measurement",
                        estimated_input_tokens=int(metadata.get("estimated_input_tokens", 0)),
                        available_input_tokens=int(metadata.get("available_input_tokens", 1)),
                        estimator_fallback_used=bool(metadata.get("estimator_fallback_used", False)),
                        knowledge_candidate_count=int(
                            metadata.get(
                                "plan_knowledge_candidate_count",
                                len(item.knowledge_context),
                            )
                        ),
                        retained_knowledge_candidate_count=int(
                            metadata.get("plan_knowledge_retained_count", 0)
                        ),
                    ),
                    provider_name=authorization.provider.name,
                    provider_model=provider_model,
                    provider_invocations=invocations,
                    provider_metered_invocations=metered,
                    provider_retries=max(0, invocations - 1),
                    input_tokens=int(metadata.get("provider_input_tokens", 0)),
                    output_tokens=int(metadata.get("provider_output_tokens", 0)),
                    cached_input_tokens=int(metadata.get("provider_cached_input_tokens", 0)),
                    latency_seconds=latency,
                    response_sha256=plan_sha256,
                )
            )
        if stops:
            break
    return InitialQuestionProviderArtifact(
        source="local_redacted_provider_output",
        dataset_id="initial-question-quality-v2",
        dataset_sha256=dataset_sha256,
        provider_name=authorization.provider.name,
        model_id=authorization.provider.model_id,
        capture_status="hard_stopped" if stops else "complete",
        hard_stop_conditions=tuple(dict.fromkeys(stops)),
        outbound_requests_attempted=outbound_requests_attempted,
        outbound_requests_metered=outbound_requests_metered,
        attempts=tuple(attempts),
    )


def _select_dataset(
    dataset: InterviewQualityDataset,
    *,
    partition: str,
    case_ids: list[str],
    scope: str,
    smoke_case_count: int,
) -> InterviewQualityDataset:
    selected = [
        case
        for case in dataset.cases
        if (partition == "all" or case.partition == partition)
        and (not case_ids or case.case_id in set(case_ids))
    ]
    if case_ids and {case.case_id for case in selected} != set(case_ids):
        missing = sorted(set(case_ids) - {case.case_id for case in selected})
        raise SystemExit(f"unknown or partition-excluded case IDs: {missing}")
    if scope == "smoke":
        selected = selected[:smoke_case_count]
    if not selected:
        raise SystemExit("no dataset cases selected")
    return dataset.model_copy(update={"cases": selected})


def _finish_blocked(
    store: EvaluationArtifactStore,
    manifest: dict,
    hard_stops: list[str],
    detail: str,
) -> int:
    manifest.update(
        {
            "updated_at": _utc_now(),
            "hard_stop_conditions": list(dict.fromkeys(hard_stops)),
            "provider_called": bool(manifest.get("provider_called")),
            "decision": "BLOCKED",
            "quality_status": "BLOCKED",
            "detail": detail,
        }
    )
    store.write_manifest(manifest)
    print(f"run_id={manifest['run_id']}")
    print(f"hard_stop_conditions={','.join(manifest['hard_stop_conditions'])}")
    return 2


def _artifact_store_is_writable(store: EvaluationArtifactStore) -> bool:
    probe = store.run_dir / ".write-probe"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def _write_json(path: Path, payload: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary.replace(path)


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


if __name__ == "__main__":
    raise SystemExit(main())
