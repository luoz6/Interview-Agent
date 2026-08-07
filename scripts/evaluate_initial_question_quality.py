from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
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
from app.services.llm import (
    LLMConfig,
    OpenAIInterviewLLM,
    resolve_plan_output_mode,
)
from app.services.prep import attach_prep_context
from app.services.provider_usage import (
    consume_provider_context_metadata,
    reset_provider_context_metadata,
)
from app.services.report_eval_artifacts import EvaluationArtifactStore
from app.services.t65_formal_execution_receipt import validate_t65_formal_route


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
    plan_output_mode = resolve_plan_output_mode(authorization.provider.model_id)
    candidate_revision, candidate_tree, worktree_clean = _candidate_identity()
    run_id = args.run_id or datetime.now(timezone.utc).strftime(
        "initial-question-t57-%Y%m%dT%H%M%SZ"
    )
    run_dir = args.out.resolve() / run_id
    if run_dir.exists() and any(run_dir.iterdir()):
        raise SystemExit(f"run directory already exists: {run_dir}")
    # Formal eligibility is owned by the formal verifier, not inferred from
    # Provider mode or engineering completeness.
    formal_route_verified = validate_t65_formal_route(
        route="builtin_candidate", receipt=None
    )
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
            "candidate_revision": candidate_revision,
            "candidate_tree": candidate_tree,
            "worktree_clean": worktree_clean,
            "provider": authorization.provider.name,
            "model": authorization.provider.model_id,
            "plan_output_mode": plan_output_mode,
            "base_url": authorization.provider.base_url,
            "provider_called": False,
            "first_data_request_sent": False,
            "hard_stop_conditions": [],
            "evidence_origin": _diagnostic_evidence_origin(args.mode, args.scope),
            "formal_evidence_eligible": formal_route_verified,
            "engineering_evidence_complete": False,
            "discovery_requests": 0,
            "planned_inference_requests": 0,
            "inference_attempted": 0,
            "inference_metered": 0,
            "retries": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "cached_input_tokens": 0,
            "estimated_cost": 0.0,
            "quality_status": "NOT_RUN",
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
        manifest["discovery_requests"] = (
            discovery.model_request_attempts + discovery.pricing_request_attempts
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
        _apply_usage_manifest(manifest, artifact)
        if all(
            isinstance(manifest.get(field), int)
            and not isinstance(manifest.get(field), bool)
            for field in (
                "input_tokens",
                "output_tokens",
                "cached_input_tokens",
            )
        ):
            manifest["estimated_cost"] = estimate_provider_cost(
                price=preflight.discovery.prices[authorization.provider.model_id],
                input_tokens=manifest["input_tokens"],
                output_tokens=manifest["output_tokens"],
                cached_input_tokens=manifest["cached_input_tokens"],
            )
        if artifact.capture_status == "hard_stopped":
            return _finish_blocked(
                store,
                manifest,
                list(artifact.hard_stop_conditions),
                "real Provider run stopped before the next request",
            )
        if args.scope == "smoke":
            smoke_price = preflight.discovery.prices[authorization.provider.model_id]
            manifest["estimated_cost"] = estimate_provider_cost(
                price=smoke_price,
                input_tokens=manifest["input_tokens"],
                output_tokens=manifest["output_tokens"],
                cached_input_tokens=manifest["cached_input_tokens"],
            )
            manifest.update(
                {
                    "updated_at": _utc_now(),
                    "completed_attempt_count": len(artifact.attempts),
                    "quality_status": "NOT_RUN_FULL_REQUIRED",
                    "decision": "NOT_RUN_FULL_REQUIRED",
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
        metrics["provider_usage"]["estimated_cost"] = (
            0.0
            if metrics["provider_usage"]["provider_invocations_this_run"] == 0
            else None
        )
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
            "planned_inference_requests": (
                metrics["provider_usage"]["provider_invocations_this_run"]
                - metrics["provider_usage"]["provider_retries"]
            ),
            "inference_attempted": metrics["provider_usage"]["provider_invocations_this_run"],
            "inference_metered": metrics["provider_usage"]["provider_metered_invocations"],
            "retries": metrics["provider_usage"]["provider_retries"],
            "input_tokens": metrics["provider_usage"]["input_tokens"],
            "output_tokens": metrics["provider_usage"]["output_tokens"],
            "cached_input_tokens": metrics["provider_usage"]["cached_input_tokens"],
            "estimated_cost": metrics["provider_usage"]["estimated_cost"],
            "automated_status": metrics["automated_status"],
            "quality_status": metrics["quality_status"],
            "decision": metrics["quality_status"],
            "evidence_origin": (
                "live_provider"
                if args.mode == "provider"
                and args.scope == "full"
                and complete_frozen_dataset
                else _diagnostic_evidence_origin(args.mode, args.scope)
            ),
            "formal_evidence_eligible": formal_route_verified,
            "engineering_evidence_complete": (
                args.mode == "provider"
                and args.scope == "full"
                and complete_frozen_dataset
                and artifact.capture_status == "complete"
                and artifact.outbound_requests_attempted
                == artifact.outbound_requests_metered
            ),
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
            plan_output_mode=resolve_plan_output_mode(
                authorization.provider.model_id
            ),
            context_window_tokens=context_window_tokens,
        )
    )
    attempts: list[InitialQuestionEvalAttempt] = []
    stops: list[str] = []
    outbound_requests_attempted = 0
    outbound_requests_metered = 0
    # The caller has already applied the requested smoke/full selection.  Do
    # not silently truncate it again here; this keeps selected_case_count and
    # the actual business samples aligned.
    selected_cases = list(dataset.cases)
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
            token_usage = _complete_provider_token_usage(metadata)
            if (
                not metadata.get("provider_usage_available")
                or metered != invocations
                or token_usage is None
            ):
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
                    input_tokens=token_usage["provider_input_tokens"],
                    output_tokens=token_usage["provider_output_tokens"],
                    cached_input_tokens=token_usage["provider_cached_input_tokens"],
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


def _apply_usage_manifest(
    manifest: dict,
    artifact: InitialQuestionProviderArtifact,
) -> None:
    attempts = artifact.attempts
    attempted = artifact.outbound_requests_attempted
    metered = artifact.outbound_requests_metered
    retries = sum(item.provider_retries for item in attempts)
    usage_fully_observed = (
        attempted == sum(item.provider_invocations for item in attempts)
        and all(
            value is not None
            for item in attempts
            for value in (
                item.input_tokens,
                item.output_tokens,
                item.cached_input_tokens,
            )
        )
    )

    def total(field: str) -> int | None:
        if attempted == 0:
            return 0
        if not usage_fully_observed:
            return None
        return sum(getattr(item, field) for item in attempts)

    manifest.update(
        {
            "provider_invocations_this_run": attempted,
            "provider_metered_invocations": metered,
            "planned_inference_requests": attempted - retries,
            "inference_attempted": attempted,
            "inference_metered": metered,
            "retries": retries,
            "input_tokens": total("input_tokens"),
            "output_tokens": total("output_tokens"),
            "cached_input_tokens": total("cached_input_tokens"),
            "estimated_cost": 0.0 if attempted == 0 else None,
        }
    )


def _complete_provider_token_usage(metadata: dict) -> dict[str, int] | None:
    result: dict[str, int] = {}
    for key in (
        "provider_input_tokens",
        "provider_output_tokens",
        "provider_cached_input_tokens",
    ):
        value = metadata.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            return None
        result[key] = value
    return result


def _diagnostic_evidence_origin(mode: str, scope: str) -> str:
    if mode == "fixture-replay":
        return "synthetic_fixture"
    if mode == "saved-replay":
        return "saved_replay"
    return "provider_smoke" if scope == "smoke" else "provider_diagnostic"


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
