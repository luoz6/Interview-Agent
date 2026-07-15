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

from app.services.llm import (
    LLMConfig,
    OpenAIInterviewLLM,
    REPORT_EVIDENCE_PROMPT_VERSION,
)
from app.services.report import ReportOutputFormatError
from app.services.report_eval_artifacts import EvaluationArtifactStore
from app.services.report_eval_case_builder import build_report_evaluation_input
from app.services.report_eval_dataset import EvaluationDataset, load_evaluation_dataset
from app.services.report_eval_metrics import AttemptResult, calculate_metrics
from app.services.report_eval_runner import EvaluationRunner
from app.services.report_rule_score import REPORT_SCORING_RUBRIC_VERSION
from app.services.report_trace import ReportTraceRecorder


class ProviderInvocationBudgetExhausted(RuntimeError):
    pass


class ProviderInvocationBudget:
    def __init__(self, limit: int) -> None:
        if limit <= 0:
            raise ValueError("provider invocation limit must be positive")
        self.limit = limit
        self.used = 0

    def consume(self) -> None:
        if self.used >= self.limit:
            raise ProviderInvocationBudgetExhausted(
                f"provider invocation budget exhausted: {self.used}/{self.limit}"
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
                "score": 0,
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
    decision = "PASS" if metrics["passed"] else "FAIL"
    lines = [
        f"# Stage 40 Release Decision: {decision}",
        "",
        "| Metric | Result | Gate |",
        "| --- | ---: | ---: |",
        f"| ranking_accuracy | {metrics['ranking_accuracy']:.3f} | >= 0.85 |",
        f"| evidence_grounding_rate | {metrics['evidence_grounding_rate']:.3f} | >= 0.90 |",
        f"| max_score_delta | {metrics['max_score_delta']:.3f} | <= 8 |",
        f"| fallback_rate | {metrics['fallback_rate']:.3f} | <= 0.05 |",
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
    config = LLMConfig.from_env()

    if args.resume:
        if not args.run_id:
            raise SystemExit("--resume requires --run-id")
        store = EvaluationArtifactStore.open(args.out / args.run_id)
        manifest = store.read_manifest()
        _validate_resume_manifest(
            manifest,
            dataset_digest=dataset_digest,
            case_ids=[case.case_id for case in dataset.cases],
            runs_per_case=args.runs_per_case,
        )
    else:
        run_id = args.run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        store = EvaluationArtifactStore.create(
            root=args.out,
            run_id=run_id,
            manifest={
                "run_id": run_id,
                "created_at": _utc_now(),
                "dataset_path": str(args.dataset),
                "dataset_version": dataset.version,
                "dataset_sha256": dataset_digest,
                "case_ids": [case.case_id for case in dataset.cases],
                "runs_per_case": args.runs_per_case,
                "target_attempts": expected_attempts,
                "provider": args.provider,
                "model": config.model,
                "base_url": config.base_url or "",
                "prompt_version": REPORT_EVIDENCE_PROMPT_VERSION,
                "rubric_version": REPORT_SCORING_RUBRIC_VERSION,
                "completed_attempts": 0,
                "provider_invocations": 0,
            },
        )
        manifest = store.read_manifest()

    budget = ProviderInvocationBudget(args.max_provider_invocations)
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
            "provider_invocations": int(manifest.get("provider_invocations", 0))
            + budget.used,
            "last_command_provider_invocations": budget.used,
            "decision": "PASS" if metrics.passed else "INCOMPLETE" if budget_exhausted else "FAIL",
        }
    )
    store.write_manifest(manifest)
    print(f"run_id={manifest['run_id']}")
    print(f"run_dir={store.run_dir}")
    print(f"completed_attempts={len(attempts)}/{expected_attempts}")
    print(f"provider_invocations={budget.used}/{budget.limit}")

    if budget_exhausted and len(attempts) < expected_attempts:
        return 2
    return 0 if metrics.passed else 1


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
    dataset_digest: str,
    case_ids: list[str],
    runs_per_case: int,
) -> None:
    expected = {
        "dataset_sha256": dataset_digest,
        "case_ids": case_ids,
        "runs_per_case": runs_per_case,
    }
    for key, value in expected.items():
        if manifest.get(key) != value:
            raise SystemExit(f"resume manifest mismatch for {key}")


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
