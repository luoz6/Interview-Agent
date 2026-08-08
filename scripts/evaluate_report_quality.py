import argparse
from dataclasses import replace
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.llm import (
    LLMConfig,
    OpenAIInterviewLLM,
    REPORT_EVIDENCE_PROMPT_VERSION,
)
from app.services.report import ReportOutputFormatError
from app.services.interview_quality_provider_authorization import (
    ProviderRunRequest,
    load_provider_authorization,
    validate_provider_run,
)
from app.services.report_eval_artifacts import (
    EvaluationArtifactStore,
    EvaluationRunLockUnavailable,
    resolve_evaluation_run_dir,
)
from app.services.report_eval_case_builder import build_report_evaluation_input
from app.services.report_eval_dataset import EvaluationDataset, load_evaluation_dataset
from app.services.report_eval_metrics import AttemptResult, calculate_metrics
from app.services.interview_quality_gate import load_gate_config
from app.services.report_eval_runner import EvaluationRunner
from app.services.report_rule_score import (
    REPORT_SCORING_RUBRIC_SHA256,
    REPORT_SCORING_RUBRIC_VERSION,
)
from app.services.report_trace import ReportTraceRecorder


class ProviderInvocationBudgetExhausted(RuntimeError):
    pass


DEFAULT_AUTHORIZATION = (
    ROOT / "config" / "interview_quality_v1_provider_authorization.json"
)
RUN_MANIFEST_SCHEMA = "report-quality-evaluation-run-v2"
PROVIDER_TASK = "T27"
FROZEN_REPORT_DATASET_SHA256 = (
    "31b5116500b990d2ec2f35c048a9ec221a8e76fcab0175123183d5e2a9991c12"
)
PROVIDER_DATA_CATEGORIES = (
    "public_technical_material",
    "synthetic_candidate_answers",
)


class ProviderInvocationBudget:
    def __init__(self, limit: int, *, prior_used: int = 0) -> None:
        if limit <= 0:
            raise ValueError("provider invocation limit must be positive")
        if prior_used < 0:
            raise ValueError("prior provider invocation usage cannot be negative")
        self.limit = limit
        self.prior_used = prior_used
        self.used = 0

    def consume(self) -> None:
        if self.prior_used + self.used >= self.limit:
            raise ProviderInvocationBudgetExhausted(
                "provider invocation budget exhausted: "
                f"{self.prior_used + self.used}/{self.limit} cumulative"
            )
        self.used += 1


class BudgetedChatModel:
    def __init__(self, inner, budget: ProviderInvocationBudget) -> None:
        self.inner = inner
        self.budget = budget

    def invoke(self, *args, **kwargs):
        self.budget.consume()
        return self.inner.invoke(*args, **kwargs)

    def with_structured_output(self, *args, **kwargs):
        structured = self.inner.with_structured_output(*args, **kwargs)
        return BudgetedChatModel(structured, self.budget)

    def __getattr__(self, name):
        return getattr(self.inner, name)


class DeepSeekCaseEvaluator:
    def __init__(self, *, chat_model, budget: ProviderInvocationBudget) -> None:
        self.chat_model = chat_model
        self.budget = budget

    def evaluate_case(self, case, *, session_id, run_number, trace_dir):
        plan, evaluation_items = build_report_evaluation_input(case)
        recorder = ReportTraceRecorder(root_dir=trace_dir)
        llm = OpenAIInterviewLLM(
            chat_model=self.chat_model,
            trace_recorder=recorder,
            report_output_mode="raw_only",
        )
        started = time.perf_counter()
        invocation_start = self.budget.used
        try:
            report = llm.generate_report(plan, evaluation_items, session_id)
            feedback = report.feedbacks[0]
            return {
                "case_id": case.case_id,
                "group_id": case.group_id,
                "quality_level": case.quality_level,
                "run_number": run_number,
                "score": feedback.score,
                "expected_score_range": list(case.expected_score_range),
                "language": _answer_language(case.answer),
                "question_type": case.question_kind,
                "answer": case.answer,
                "observed": [
                    value
                    for item in feedback.dimension_evidence
                    for value in item.get("observed", [])
                ],
                "required_observations": case.required_observations,
                "forbidden_claims": case.forbidden_claims,
                "applicable_dimensions": feedback.applicable_dimensions,
                "expected_applicable_dimensions": case.expected_applicable_dimensions,
                "fallback": report.is_fallback,
                "output_text": " ".join(
                    [feedback.rationale, feedback.critique, feedback.better_answer]
                ),
                "latency_seconds": round(time.perf_counter() - started, 3),
                "provider_invocations": self.budget.used - invocation_start,
            }
        except ReportOutputFormatError as exc:
            return {
                "case_id": case.case_id,
                "group_id": case.group_id,
                "quality_level": case.quality_level,
                "run_number": run_number,
                "score": None,
                "expected_score_range": list(case.expected_score_range),
                "language": _answer_language(case.answer),
                "question_type": case.question_kind,
                "answer": case.answer,
                "observed": [],
                "required_observations": case.required_observations,
                "forbidden_claims": case.forbidden_claims,
                "applicable_dimensions": case.expected_applicable_dimensions,
                "expected_applicable_dimensions": case.expected_applicable_dimensions,
                "fallback": True,
                "output_text": str(exc),
                "latency_seconds": round(time.perf_counter() - started, 3),
                "provider_invocations": self.budget.used - invocation_start,
            }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evaluate report scoring with DeepSeek")
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path("tests/golden/report_quality_v1.json"),
    )
    parser.add_argument("--runs-per-case", type=int, default=2)
    parser.add_argument("--provider", default="deepseek")
    parser.add_argument("--authorization", type=Path, default=DEFAULT_AUTHORIZATION)
    parser.add_argument("--out", type=Path, default=Path("reports/stage40"))
    parser.add_argument("--run-id")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--case-id")
    parser.add_argument("--group-id")
    parser.add_argument(
        "--max-provider-invocations",
        type=int,
        default=int(os.getenv("STAGE40_MAX_PROVIDER_INVOCATIONS", "50")),
    )
    return parser


def render_markdown(metrics: dict) -> str:
    config = load_gate_config()
    rules = {
        "ranking_accuracy": config.resolve_rule(
            "report_scoring.pairwise_ranking_accuracy"
        ),
        "evidence_grounding_rate": config.resolve_rule(
            "report_scoring.evidence_grounding_rate"
        ),
        "max_score_delta": config.resolve_rule(
            "report_scoring.provider_repeat_max_delta"
        ),
        "fallback_rate": config.resolve_rule("report_scoring.fallback_rate"),
    }

    def gate_text(name: str) -> str:
        rule = rules[name]
        symbols = {"gte": ">=", "lte": "<=", "eq": "=="}
        return f"{symbols[rule.operator]} {rule.threshold:g}"

    decision = metrics.get("decision", "PASS" if metrics["passed"] else "FAIL")
    lines = [
        f"# Stage 40 Release Decision: {decision}",
        "",
        "| Metric | Result | Gate |",
        "| --- | ---: | ---: |",
        f"| ranking_accuracy | {metrics['ranking_accuracy']:.3f} | {gate_text('ranking_accuracy')} |",
        f"| evidence_grounding_rate | {metrics['evidence_grounding_rate']:.3f} | {gate_text('evidence_grounding_rate')} |",
        f"| max_score_delta | {metrics['max_score_delta']:.3f} | {gate_text('max_score_delta')} |",
        f"| fallback_rate | {metrics['fallback_rate']:.3f} | {gate_text('fallback_rate')} |",
        f"| expected_range_attempt_hit_rate | {metrics.get('expected_range_attempt_hit_rate', 0):.3f} | GateConfig |",
        f"| strong_attempt_hit_rate | {metrics.get('strong_attempt_hit_rate', 0):.3f} | GateConfig |",
        f"| interval_outside_mae | {metrics.get('interval_outside_mae', 0):.3f} | GateConfig |",
        f"| expert_score_spearman | {metrics.get('expert_score_spearman', 0):.3f} | GateConfig |",
        "",
        f"- completed_attempts: {metrics['completed_attempt_count']}/{metrics['expected_attempt_count']}",
        f"- failed_gates: {', '.join(metrics['failed_gates']) or 'none'}",
        f"- blocking_failures: {len(metrics['blocking_failures'])}",
    ]
    if metrics["blocking_failures"]:
        lines.extend(["", "## Blocking Failures", ""])
        lines.extend(
            f"- `{json.dumps(item, ensure_ascii=False)}`"
            for item in metrics["blocking_failures"]
        )
    lines.extend(
        [
            "",
            "## Focused Rerun",
            "",
            "```powershell",
            "F:\\python3.11\\python.exe -m scripts.evaluate_report_quality --resume --run-id <run-id> --max-provider-invocations 50",
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.resume and not args.run_id:
        raise SystemExit("--resume requires --run-id")
    run_id = args.run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    try:
        run_dir = resolve_evaluation_run_dir(args.out, run_id)
    except ValueError as exc:
        raise SystemExit(f"invalid --run-id: {exc}") from exc
    _load_local_env(Path(".env"))
    if args.case_id and args.group_id:
        raise SystemExit("--case-id and --group-id are mutually exclusive")

    dataset_path = args.dataset.resolve()
    dataset = _filter_dataset(
        load_evaluation_dataset(dataset_path),
        case_id=args.case_id,
        group_id=args.group_id,
    )
    expected_attempts = dataset.target_attempt_count(runs_per_case=args.runs_per_case)
    dataset_digest = hashlib.sha256(dataset_path.read_bytes()).hexdigest()
    authorization_path = args.authorization.resolve()
    authorization = load_provider_authorization(authorization_path)
    authorization_sha256 = hashlib.sha256(
        authorization_path.read_bytes()
    ).hexdigest()
    config = LLMConfig.from_env()
    if config.base_url is None:
        config = replace(config, base_url=authorization.provider.base_url)
    if (
        args.provider.casefold() != authorization.provider.name.casefold()
        or config.model != authorization.provider.model_id
        or config.base_url.rstrip("/")
        != authorization.provider.base_url.rstrip("/")
    ):
        raise SystemExit("Provider configuration is outside the frozen authorization")
    base_url_host = urlparse(config.base_url).hostname or ""
    redaction_preflight_passed = (
        dataset_path
        == (ROOT / "tests/golden/report_quality_v1.json").resolve()
        and dataset_digest == FROZEN_REPORT_DATASET_SHA256
        and dataset.version == "report-quality-v1"
    )
    provider_request = ProviderRunRequest(
        task=PROVIDER_TASK,
        provider_name=authorization.provider.name,
        base_url=config.base_url,
        model_id=config.model,
        data_categories=set(PROVIDER_DATA_CATEGORIES),
        redaction_preflight_passed=redaction_preflight_passed,
        usage_metering_available=True,
        evidence_persistence_available=_evidence_persistence_available(
            run_dir
        ),
    )
    authorization_stops = list(
        validate_provider_run(authorization, provider_request)
    )
    if not redaction_preflight_passed:
        authorization_stops.append("GATE_CONFIG_OR_DATASET_DRIFT")
    authorization_stops = list(dict.fromkeys(authorization_stops))
    authorization_receipt = {
        "schema_version": "report-quality-provider-authorization-receipt-v1",
        "task": PROVIDER_TASK,
        "authorization_id": authorization.authorization_id,
        "authorization_sha256": authorization_sha256,
        "provider": authorization.provider.name,
        "model": config.model,
        "base_url_host": base_url_host,
        "data_categories": list(PROVIDER_DATA_CATEGORIES),
        "data_scope": {
            "dataset_sha256": dataset_digest,
            "dataset_version": dataset.version,
            "case_ids": [case.case_id for case in dataset.cases],
            "runs_per_case": args.runs_per_case,
        },
        "redaction_preflight_passed": redaction_preflight_passed,
        "usage_metering_available": True,
        "usage_metering_mode": "provider_invocation_budget",
        "evidence_persistence_available": (
            provider_request.evidence_persistence_available
        ),
        "max_provider_invocations": args.max_provider_invocations,
        "stops": authorization_stops,
        "passed": not authorization_stops,
    }
    authorization_receipt_sha256 = _canonical_sha256(
        authorization_receipt
    )
    if authorization_stops:
        raise SystemExit(
            "Provider authorization preflight blocked: "
            + ", ".join(authorization_stops)
        )

    if args.resume:
        store = EvaluationArtifactStore.open(root=args.out, run_id=run_id)
    else:
        store = EvaluationArtifactStore.create(
            root=args.out,
            run_id=run_id,
            manifest={
                "schema_version": RUN_MANIFEST_SCHEMA,
                "run_id": run_id,
                "created_at": _utc_now(),
                "dataset_path": str(dataset_path),
                "dataset_version": dataset.version,
                "dataset_sha256": dataset_digest,
                "case_ids": [case.case_id for case in dataset.cases],
                "runs_per_case": args.runs_per_case,
                "target_attempts": expected_attempts,
                "provider": authorization.provider.name,
                "model": config.model,
                "base_url": config.base_url or "",
                "authorization_id": authorization.authorization_id,
                "authorization_sha256": authorization_sha256,
                "authorization_receipt": authorization_receipt,
                "authorization_receipt_sha256": (
                    authorization_receipt_sha256
                ),
                "max_provider_invocations": args.max_provider_invocations,
                "prompt_version": REPORT_EVIDENCE_PROMPT_VERSION,
                "rubric_version": REPORT_SCORING_RUBRIC_VERSION,
                "rubric_sha256": REPORT_SCORING_RUBRIC_SHA256,
                "completed_attempts": 0,
                "provider_invocations": 0,
            },
        )
    try:
        with store.exclusive_run_lock():
            manifest = store.read_manifest()
            if args.resume:
                _validate_resume_manifest(
                    manifest,
                    run_id=run_id,
                    dataset_digest=dataset_digest,
                    dataset_version=dataset.version,
                    case_ids=[case.case_id for case in dataset.cases],
                    runs_per_case=args.runs_per_case,
                    provider=authorization.provider.name,
                    model=config.model,
                    base_url_host=base_url_host,
                    authorization_id=authorization.authorization_id,
                    authorization_sha256=authorization_sha256,
                    authorization_receipt=authorization_receipt,
                    authorization_receipt_sha256=(
                        authorization_receipt_sha256
                    ),
                    max_provider_invocations=args.max_provider_invocations,
                )
            prior_used = manifest.get("provider_invocations", 0)
            if (
                isinstance(prior_used, bool)
                or not isinstance(prior_used, int)
                or prior_used < 0
            ):
                raise SystemExit("resume manifest provider_invocations is invalid")
            pending = store.pending_attempts(
                [case.case_id for case in dataset.cases],
                runs_per_case=args.runs_per_case,
            )
            if pending and prior_used >= args.max_provider_invocations:
                raise SystemExit(
                    "cumulative provider invocation budget exhausted before "
                    "pending attempts"
                )
            return _run_evaluation_locked(
                args=args,
                dataset=dataset,
                expected_attempts=expected_attempts,
                store=store,
                manifest=manifest,
                config=config,
                prior_used=prior_used,
            )
    except EvaluationRunLockUnavailable as exc:
        raise SystemExit(
            "evaluation run is locked by another process; Provider not called"
        ) from exc


def _run_evaluation_locked(
    *, args, dataset, expected_attempts, store, manifest, config, prior_used: int
) -> int:
    budget = ProviderInvocationBudget(
        args.max_provider_invocations, prior_used=prior_used
    )
    real_model = OpenAIInterviewLLM._build_chat_model(config)
    evaluator = DeepSeekCaseEvaluator(
        chat_model=BudgetedChatModel(real_model, budget),
        budget=budget,
    )
    runner = EvaluationRunner(evaluator=evaluator, artifact_store=store)
    budget_exhausted = False
    try:
        runner.run(dataset=dataset, runs_per_case=args.runs_per_case)
    except Exception as exc:
        if _is_budget_exhausted(exc):
            budget_exhausted = True
        else:
            raise

    attempts = [
        AttemptResult.model_validate(item)
        for item in store.load_normalized_attempts()
        if item.get("case_id") in {case.case_id for case in dataset.cases}
    ]
    metrics = calculate_metrics(attempts, expected_attempt_count=expected_attempts)
    metrics_payload = metrics.model_dump(mode="json")
    store.write_metrics(metrics_payload)
    store.write_report(render_markdown(metrics_payload))

    manifest.update(
        {
            "updated_at": _utc_now(),
            "completed_attempts": len(attempts),
            "provider_invocations": prior_used + budget.used,
            "last_command_provider_invocations": budget.used,
            "decision": (
                "INCOMPLETE"
                if budget_exhausted
                else metrics.decision
            ),
        }
    )
    store.write_manifest(manifest)
    print(f"run_id={manifest['run_id']}")
    print(f"run_dir={store.run_dir}")
    print(f"completed_attempts={len(attempts)}/{expected_attempts}")
    print(f"provider_invocations_this_run={budget.used}/{budget.limit}")
    print(
        "provider_invocations_cumulative="
        f"{prior_used + budget.used}/{budget.limit}"
    )

    if budget_exhausted and len(attempts) < expected_attempts:
        return 2
    if metrics.decision == "INSUFFICIENT_SAMPLE":
        return 2
    return 0 if metrics.passed else 1


def _answer_language(value: str) -> str:
    has_chinese = any("\u4e00" <= char <= "\u9fff" for char in value)
    has_latin = any("a" <= char.lower() <= "z" for char in value)
    if has_chinese and has_latin:
        return "mixed"
    if has_chinese:
        return "zh"
    if has_latin:
        return "en"
    return "unknown"


def _filter_dataset(
    dataset: EvaluationDataset,
    *,
    case_id: str | None,
    group_id: str | None,
):
    if case_id:
        cases = [case for case in dataset.cases if case.case_id == case_id]
        if not cases:
            raise SystemExit(f"unknown case_id: {case_id}")
        return _DatasetSelection(dataset.version, cases)
    if group_id:
        cases = [case for case in dataset.cases if case.group_id == group_id]
        if not cases:
            raise SystemExit(f"unknown group_id: {group_id}")
        return _DatasetSelection(dataset.version, cases)
    return dataset


class _DatasetSelection:
    def __init__(self, version: str, cases: list) -> None:
        self.version = version
        self.cases = cases

    def target_attempt_count(self, *, runs_per_case: int) -> int:
        if runs_per_case <= 0:
            raise ValueError("runs_per_case must be positive")
        return len(self.cases) * runs_per_case


def _validate_resume_manifest(
    manifest: dict,
    *,
    run_id: str,
    dataset_digest: str,
    dataset_version: str,
    case_ids: list[str],
    runs_per_case: int,
    provider: str,
    model: str,
    base_url_host: str,
    authorization_id: str,
    authorization_sha256: str,
    authorization_receipt: dict,
    authorization_receipt_sha256: str,
    max_provider_invocations: int,
) -> None:
    # v1 manifests did not bind enough execution semantics to authorize a
    # resumed Provider run. They are deliberately rejected rather than treated
    # as backward-compatible authorization receipts.
    expected = {
        "schema_version": RUN_MANIFEST_SCHEMA,
        "run_id": run_id,
        "dataset_sha256": dataset_digest,
        "dataset_version": dataset_version,
        "case_ids": case_ids,
        "runs_per_case": runs_per_case,
        "provider": provider,
        "model": model,
        "base_url_host": base_url_host,
        "prompt_version": REPORT_EVIDENCE_PROMPT_VERSION,
        "rubric_version": REPORT_SCORING_RUBRIC_VERSION,
        "rubric_sha256": REPORT_SCORING_RUBRIC_SHA256,
        "authorization_id": authorization_id,
        "authorization_sha256": authorization_sha256,
        "authorization_receipt": authorization_receipt,
        "authorization_receipt_sha256": authorization_receipt_sha256,
        "max_provider_invocations": max_provider_invocations,
    }
    for key, value in expected.items():
        if manifest.get(key) != value:
            raise SystemExit(f"resume manifest mismatch for {key}")


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


def _evidence_persistence_available(run_dir: Path) -> bool:
    candidate = run_dir.parent.resolve()
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    return candidate.is_dir() and os.access(candidate, os.W_OK)


def _load_local_env(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        os.environ.setdefault(name.strip(), value.strip())


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _is_budget_exhausted(exc: BaseException) -> bool:
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, ProviderInvocationBudgetExhausted):
            return True
        current = current.__cause__ or current.__context__
    return False


if __name__ == "__main__":
    sys.exit(main())
