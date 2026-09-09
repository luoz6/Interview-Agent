import argparse
from dataclasses import replace
import hashlib
import inspect
import json
import os
import re
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
    REPORT_EVIDENCE_PROMPT_SHA256,
    REPORT_EVIDENCE_PROMPT_VERSION,
)
from app.services.evaluator_candidate_identity import (
    capture_evaluator_candidate_identity,
)
from app.services.provider_usage import (
    consume_provider_context_metadata,
    reset_provider_context_metadata,
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
from app.services.report_eval_metrics import (
    AttemptResult,
    calculate_metrics,
    classify_forbidden_claims,
)
from app.services.interview_quality_gate import (
    DEFAULT_GATE_CONFIG_PATH,
    gate_config_sha256,
    load_gate_config,
)
from app.services.report_eval_runner import EvaluationRunner
from app.services.report_rule_score import (
    REPORT_SCORING_RUBRIC_SHA256,
    REPORT_SCORING_RUBRIC_VERSION,
)
from app.services.report_trace import ReportTraceRecorder
from app.services.report_answer_guidance import REPORT_ANSWER_GUIDANCE_VERSION
from app.services.report_summary import (
    REPORT_SUMMARY_PROMPT_SHA256,
    REPORT_SUMMARY_PROMPT_VERSION,
)


class ProviderInvocationBudgetExhausted(RuntimeError):
    pass


class ProviderEvidenceContractError(RuntimeError):
    stop_evaluation = True

    def __init__(self, hard_stop_condition: str, artifact_payload: dict) -> None:
        super().__init__(hard_stop_condition)
        self.hard_stop_condition = hard_stop_condition
        self.artifact_payload = artifact_payload


DEFAULT_AUTHORIZATION = (
    ROOT / "config" / "interview_quality_v1_provider_authorization.json"
)
RUN_MANIFEST_SCHEMA = "report-quality-evaluation-run-v3"
ATTEMPT_SCHEMA = "report-quality-evaluation-attempt-v2"
PROVIDER_EVIDENCE_CONTRACT_VERSION = "report-provider-evidence-v1"
OWNERSHIP_CONTRACT_VERSION = "report-evaluation-ownership-v1"
PARTITION_MODE = "unpartitioned_development_construction_set"
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
    def __init__(
        self,
        *,
        chat_model,
        budget: ProviderInvocationBudget,
        expected_model: str = "deepseek-v4-pro",
    ) -> None:
        self.chat_model = (
            chat_model.inner
            if isinstance(chat_model, BudgetedChatModel)
            else chat_model
        )
        self.budget = budget
        self.expected_model = expected_model
        self._budget_start = budget.used
        self._retry_count = 0

    def begin_case_attempt(self) -> None:
        reset_provider_context_metadata()
        self._budget_start = self.budget.used
        self._retry_count = 0

    def note_retry(self) -> None:
        self._retry_count += 1

    def abandon_case_attempt(self) -> None:
        consume_provider_context_metadata()

    def complete_case_attempt(
        self,
        normalized: dict,
        *,
        latency_seconds: float,
    ) -> dict:
        metadata = consume_provider_context_metadata()
        try:
            evidence = self._validated_provider_evidence(
                metadata,
                latency_seconds=latency_seconds,
            )
        except ProviderEvidenceContractError as exc:
            exc.evidence_consumed = True
            raise
        normalized.update(evidence)
        attempt = AttemptResult.model_validate(normalized)
        provider_claims, backend_claims = classify_forbidden_claims(attempt)
        normalized.update(
            {
                "provider_forbidden_claim": bool(provider_claims),
                "backend_guidance_forbidden_claim": bool(backend_claims),
                "provider_forbidden_claims_detected": provider_claims,
                "backend_guidance_forbidden_claims_detected": backend_claims,
            }
        )
        return normalized

    def fail_case_attempt(
        self,
        exc: Exception,
        *,
        latency_seconds: float,
    ) -> Exception:
        metadata = consume_provider_context_metadata()
        try:
            self._validated_provider_evidence(
                metadata,
                latency_seconds=latency_seconds,
            )
        except ProviderEvidenceContractError as contract_error:
            contract_error.evidence_consumed = True
            return contract_error
        return exc

    def _validated_provider_evidence(
        self,
        metadata: dict,
        *,
        latency_seconds: float,
    ) -> dict:
        invocations = self.budget.used - self._budget_start
        attempted = metadata.get("provider_attempt_count")
        metered = metadata.get("provider_metered_attempt_count")
        models = metadata.get("provider_response_models", [])
        response_hashes = metadata.get("provider_response_id_sha256s", [])
        safe = {
            "schema_version": "report-provider-evidence-failure-v1",
            "provider_evidence_contract_version": (
                PROVIDER_EVIDENCE_CONTRACT_VERSION
            ),
            "provider_invocations": invocations,
            "provider_metered_invocations": (
                metered if isinstance(metered, int) and not isinstance(metered, bool) else 0
            ),
            "provider_retries": self._retry_count,
            "latency_seconds": round(max(0.0, latency_seconds), 3),
            "actual_provider_models": (
                list(models) if isinstance(models, list) else []
            ),
            "provider_response_id_sha256s": (
                list(response_hashes) if isinstance(response_hashes, list) else []
            ),
        }
        for target, source in (
            ("input_tokens", "provider_input_tokens"),
            ("output_tokens", "provider_output_tokens"),
            ("cached_input_tokens", "provider_cached_input_tokens"),
        ):
            value = metadata.get(source)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                safe[target] = value

        stop: str | None = None
        if attempted != invocations or invocations <= 0:
            stop = "PROVIDER_INVOCATION_EVIDENCE_MISMATCH"
        elif (
            metered != attempted
            or metadata.get("provider_usage_available") is not True
            or any(key not in safe for key in ("input_tokens", "output_tokens", "cached_input_tokens"))
        ):
            stop = "PROVIDER_USAGE_UNMETERED"
        elif (
            not isinstance(models, list)
            or len(models) != attempted
            or any(not isinstance(value, str) or not value for value in models)
        ):
            stop = "PROVIDER_MODEL_METADATA_MISSING"
        elif any(value != self.expected_model for value in models):
            stop = "PROVIDER_MODEL_MISMATCH"
        elif (
            not isinstance(response_hashes, list)
            or len(response_hashes) != attempted
            or any(
                not isinstance(value, str)
                or re.fullmatch(r"[0-9a-f]{64}", value) is None
                for value in response_hashes
            )
        ):
            stop = "PROVIDER_RESPONSE_ID_MISSING"
        elif self._retry_count != max(0, invocations - 1):
            stop = "PROVIDER_RETRY_EVIDENCE_MISMATCH"
        if stop is not None:
            safe["hard_stop_condition"] = stop
            raise ProviderEvidenceContractError(stop, safe)
        return {
            "actual_provider_model": models[0],
            "provider_invocations": invocations,
            "provider_metered_invocations": metered,
            "provider_response_id_sha256s": list(response_hashes),
            "provider_retries": self._retry_count,
            "input_tokens": safe["input_tokens"],
            "output_tokens": safe["output_tokens"],
            "cached_input_tokens": safe["cached_input_tokens"],
            "latency_seconds": safe["latency_seconds"],
        }

    def evaluate_case(self, case, *, session_id, run_number, trace_dir):
        plan, evaluation_items = build_report_evaluation_input(case)
        recorder = ReportTraceRecorder(root_dir=trace_dir)
        provider_owned_results: list[dict] = []
        llm = OpenAIInterviewLLM(
            chat_model=self.chat_model,
            trace_recorder=recorder,
            report_output_mode="raw_only",
            provider_attempt_hook=self.budget.consume,
            report_evidence_observer=(
                lambda results: provider_owned_results.extend(results)
            ),
        )
        started = time.perf_counter()
        invocation_start = self.budget.used
        try:
            report = llm.generate_report(plan, evaluation_items, session_id)
            feedback = report.feedbacks[0]
            provider_owned = next(
                (
                    item
                    for item in provider_owned_results
                    if item.get("question_id") == case.case_id
                ),
                {
                    "question_id": case.case_id,
                    "observed": [],
                    "rationale": "",
                    "critique": "",
                    "highlights": [],
                    "reference_ids": [],
                },
            )
            backend_owned = {
                "numeric_score": feedback.score,
                "dimension_scores": feedback.dimension_scores.model_dump(),
                "answer_guidance": feedback.better_answer,
            }
            return {
                "schema_version": ATTEMPT_SCHEMA,
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
                    str(value)
                    for value in provider_owned.get("observed", [])
                ],
                "required_observations": case.required_observations,
                "forbidden_claims": case.forbidden_claims,
                "applicable_dimensions": feedback.applicable_dimensions,
                "expected_applicable_dimensions": case.expected_applicable_dimensions,
                "fallback": report.is_fallback,
                "provider_owned": provider_owned,
                "backend_owned": backend_owned,
                # Compatibility field remains Provider-only. Backend answer
                # guidance is never routed to the evidence-Prompt owner.
                "output_text": " ".join(
                    str(value)
                    for value in (
                        provider_owned.get("rationale", ""),
                        provider_owned.get("critique", ""),
                        *provider_owned.get("highlights", []),
                    )
                    if str(value)
                ),
                "latency_seconds": round(time.perf_counter() - started, 3),
                "provider_invocations": self.budget.used - invocation_start,
            }
        except ReportOutputFormatError as exc:
            return {
                "schema_version": ATTEMPT_SCHEMA,
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
                "provider_owned": {
                    "question_id": case.case_id,
                    "observed": [],
                    "rationale": "",
                    "critique": "",
                    "highlights": [],
                    "reference_ids": [],
                },
                "backend_owned": {
                    "numeric_score": None,
                    "dimension_scores": {},
                    "answer_guidance": "",
                },
                "output_text": "",
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
        f"- provider_forbidden_claim_count: {metrics.get('provider_forbidden_claim_count', 0)}",
        f"- backend_guidance_forbidden_claim_count: {metrics.get('backend_guidance_forbidden_claim_count', 0)}",
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


def _provisional_manifest(
    *,
    run_id: str,
    dataset_path: Path,
    candidate_fields: dict,
) -> dict:
    return {
        "schema_version": RUN_MANIFEST_SCHEMA,
        "run_id": run_id,
        "created_at": _utc_now(),
        "dataset_path": str(dataset_path),
        "dataset_version": None,
        "dataset_sha256": None,
        "case_ids": [],
        "runs_per_case": None,
        "target_attempts": None,
        "provider": None,
        "model": None,
        "authorization_id": None,
        "authorization_sha256": None,
        "authorization_receipt": None,
        "authorization_receipt_sha256": None,
        "max_provider_invocations": None,
        "prompt_version": REPORT_EVIDENCE_PROMPT_VERSION,
        "prompt_sha256": REPORT_EVIDENCE_PROMPT_SHA256,
        "rubric_version": REPORT_SCORING_RUBRIC_VERSION,
        "rubric_sha256": REPORT_SCORING_RUBRIC_SHA256,
        "summary_prompt_version": REPORT_SUMMARY_PROMPT_VERSION,
        "summary_prompt_sha256": REPORT_SUMMARY_PROMPT_SHA256,
        "answer_guidance_version": REPORT_ANSWER_GUIDANCE_VERSION,
        "gate_config_sha256": gate_config_sha256(DEFAULT_GATE_CONFIG_PATH),
        "partition_mode": PARTITION_MODE,
        "blind_test_consumed": False,
        "provider_evidence_contract_version": PROVIDER_EVIDENCE_CONTRACT_VERSION,
        "ownership_contract_version": OWNERSHIP_CONTRACT_VERSION,
        "attempt_schema_version": ATTEMPT_SCHEMA,
        "completed_attempts": 0,
        "provider_invocations": 0,
        "provider_metered_invocations": 0,
        "provider_retries": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cached_input_tokens": 0,
        "actual_provider_model": None,
        "actual_provider_models": [],
        "provider_response_id_sha256s": [],
        "provider_response_id_count": 0,
        "provider_usage_complete": None,
        "provider_called": False,
        "first_data_request_sent": False,
        "preflight_status": "pending",
        "terminal": False,
        **candidate_fields,
    }


def _authorized_manifest_fields(
    *,
    authorization,
    config,
    authorization_sha256: str,
    authorization_receipt: dict,
    authorization_receipt_sha256: str,
    args,
    prompt_sha256: str,
) -> dict:
    return {
        "provider": authorization.provider.name,
        "model": config.model,
        "base_url": config.base_url or "",
        "authorization_id": authorization.authorization_id,
        "authorization_sha256": authorization_sha256,
        "authorization_receipt": authorization_receipt,
        "authorization_receipt_sha256": authorization_receipt_sha256,
        "max_provider_invocations": args.max_provider_invocations,
        "prompt_version": REPORT_EVIDENCE_PROMPT_VERSION,
        "prompt_sha256": prompt_sha256,
        "preflight_status": "passed",
        "terminal": False,
    }


def _terminal_preflight(
    store: EvaluationArtifactStore,
    manifest: dict,
    hard_stop_condition: str,
    message: str,
    *,
    all_conditions: list[str] | None = None,
) -> None:
    conditions = list(dict.fromkeys(all_conditions or [hard_stop_condition]))
    existing_invocations = manifest.get("provider_invocations", 0)
    if not isinstance(existing_invocations, int) or isinstance(
        existing_invocations, bool
    ):
        existing_invocations = 0
    existing_metered = manifest.get("provider_metered_invocations", 0)
    if not isinstance(existing_metered, int) or isinstance(existing_metered, bool):
        existing_metered = 0
    existing_retries = manifest.get("provider_retries", 0)
    if not isinstance(existing_retries, int) or isinstance(existing_retries, bool):
        existing_retries = 0
    manifest.update(
        {
            "updated_at": _utc_now(),
            "preflight_status": "blocked",
            "decision": "BLOCKED_PREFLIGHT",
            "hard_stop_conditions": conditions,
            "terminal": True,
            "provider_called": existing_invocations > 0,
            "first_data_request_sent": existing_invocations > 0,
            "provider_invocations": existing_invocations,
            "provider_metered_invocations": existing_metered,
            "provider_retries": existing_retries,
        }
    )
    store.write_manifest(manifest)
    raise SystemExit(f"{message}: {hard_stop_condition}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.resume and not args.run_id:
        raise SystemExit("--resume requires --run-id")
    prompt_sha256 = _prompt_sha256()
    run_id = args.run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    try:
        run_dir = resolve_evaluation_run_dir(args.out, run_id)
    except ValueError as exc:
        raise SystemExit(f"invalid --run-id: {exc}") from exc
    candidate_identity = capture_evaluator_candidate_identity(ROOT)
    dataset_path = args.dataset.resolve()
    if args.resume:
        try:
            store = EvaluationArtifactStore.open(root=args.out, run_id=run_id)
        except FileNotFoundError as exc:
            raise SystemExit("resume artifact is missing") from exc
    else:
        store = EvaluationArtifactStore.create(
            root=args.out,
            run_id=run_id,
            manifest=_provisional_manifest(
                run_id=run_id,
                dataset_path=dataset_path,
                candidate_fields=candidate_identity.manifest_fields(),
            ),
        )
    try:
        with store.exclusive_run_lock():
            manifest = store.read_manifest()
            if args.case_id and args.group_id:
                _terminal_preflight(
                    store,
                    manifest,
                    "DATASET_SELECTION_CONFLICT",
                    "--case-id and --group-id are mutually exclusive",
                )
            if args.max_provider_invocations <= 0:
                _terminal_preflight(
                    store,
                    manifest,
                    "PROVIDER_BUDGET_INVALID",
                    "provider invocation limit must be positive",
                )
            try:
                _load_local_env(Path(".env"))
                dataset = _filter_dataset(
                    load_evaluation_dataset(dataset_path),
                    case_id=args.case_id,
                    group_id=args.group_id,
                )
                expected_attempts = dataset.target_attempt_count(
                    runs_per_case=args.runs_per_case
                )
                dataset_digest = hashlib.sha256(dataset_path.read_bytes()).hexdigest()
            except (Exception, SystemExit) as exc:
                _terminal_preflight(
                    store,
                    manifest,
                    "DATASET_PREFLIGHT_FAILED",
                    "Report dataset preflight blocked",
                )
                raise AssertionError("unreachable") from exc
            if not args.resume:
                manifest.update(
                    {
                        "dataset_version": dataset.version,
                        "dataset_sha256": dataset_digest,
                        "case_ids": [case.case_id for case in dataset.cases],
                        "runs_per_case": args.runs_per_case,
                        "target_attempts": expected_attempts,
                    }
                )
                store.write_manifest(manifest)
            try:
                authorization_path = args.authorization.resolve()
                authorization = load_provider_authorization(authorization_path)
                authorization_sha256 = hashlib.sha256(
                    authorization_path.read_bytes()
                ).hexdigest()
            except Exception as exc:
                _terminal_preflight(
                    store,
                    manifest,
                    "PROVIDER_AUTHORIZATION_UNAVAILABLE",
                    "Provider authorization preflight blocked",
                )
                raise AssertionError("unreachable") from exc
            manifest.update(
                {
                    "authorization_id": authorization.authorization_id,
                    "authorization_sha256": authorization_sha256,
                    "authorized_provider": authorization.provider.name,
                    "authorized_model": authorization.provider.model_id,
                }
            )
            store.write_manifest(manifest)
            try:
                config = LLMConfig.from_env()
            except Exception as exc:
                _terminal_preflight(
                    store,
                    manifest,
                    "PROVIDER_CONFIG_UNAVAILABLE",
                    "Provider configuration preflight blocked",
                )
                raise AssertionError("unreachable") from exc
            if config.base_url is None:
                config = replace(config, base_url=authorization.provider.base_url)
            if (
                args.provider.casefold() != authorization.provider.name.casefold()
                or config.model != authorization.provider.model_id
                or config.base_url.rstrip("/")
                != authorization.provider.base_url.rstrip("/")
            ):
                _terminal_preflight(
                    store,
                    manifest,
                    "PROVIDER_CONFIG_OUTSIDE_AUTHORIZATION",
                    "Provider configuration is outside the frozen authorization",
                )
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
                "schema_version": "report-quality-provider-authorization-receipt-v2",
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
                    "partition_mode": PARTITION_MODE,
                    "blind_test_consumed": False,
                },
                "redaction_preflight_passed": redaction_preflight_passed,
                "usage_metering_available": None,
                "usage_metering_required": True,
                "usage_metering_mode": "provider_response_metadata_required",
                "evidence_persistence_available": (
                    provider_request.evidence_persistence_available
                ),
                "max_provider_invocations": args.max_provider_invocations,
                "stops": authorization_stops,
                "passed": not authorization_stops,
            }
            authorization_receipt_sha256 = _canonical_sha256(authorization_receipt)
            if authorization_stops:
                _terminal_preflight(
                    store,
                    manifest,
                    authorization_stops[0],
                    "Provider authorization preflight blocked",
                    all_conditions=authorization_stops,
                )
            if args.resume:
                try:
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
                        authorization_receipt_sha256=authorization_receipt_sha256,
                        prompt_sha256=prompt_sha256,
                        max_provider_invocations=args.max_provider_invocations,
                        candidate_fields=candidate_identity.manifest_fields(),
                        gate_config_sha256=gate_config_sha256(DEFAULT_GATE_CONFIG_PATH),
                    )
                except SystemExit as exc:
                    _terminal_preflight(
                        store,
                        manifest,
                        "RESUME_MANIFEST_MISMATCH",
                        "resume manifest mismatch",
                    )
            else:
                manifest.update(
                    _authorized_manifest_fields(
                        authorization=authorization,
                        config=config,
                        authorization_sha256=authorization_sha256,
                        authorization_receipt=authorization_receipt,
                        authorization_receipt_sha256=authorization_receipt_sha256,
                        args=args,
                        prompt_sha256=prompt_sha256,
                    )
                )
                store.write_manifest(manifest)
            prior_used = manifest.get("provider_invocations", 0)
            if (
                isinstance(prior_used, bool)
                or not isinstance(prior_used, int)
                or prior_used < 0
            ):
                _terminal_preflight(
                    store,
                    manifest,
                    "RESUME_PROVIDER_INVOCATIONS_INVALID",
                    "resume manifest provider_invocations is invalid",
                )
            pending = store.pending_attempts(
                [case.case_id for case in dataset.cases],
                runs_per_case=args.runs_per_case,
            )
            if pending and prior_used >= args.max_provider_invocations:
                _terminal_preflight(
                    store,
                    manifest,
                    "PROVIDER_BUDGET_EXHAUSTED_BEFORE_PENDING",
                    "cumulative provider invocation budget exhausted before pending attempts",
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
    try:
        real_model = OpenAIInterviewLLM._build_chat_model(config)
    except Exception as exc:
        _terminal_preflight(
            store,
            manifest,
            "PROVIDER_MODEL_CONSTRUCTION_FAILED",
            "Provider model construction blocked",
        )
        raise AssertionError("unreachable") from exc
    evaluator = DeepSeekCaseEvaluator(
        chat_model=real_model,
        budget=budget,
        expected_model=config.model,
    )
    runner = EvaluationRunner(evaluator=evaluator, artifact_store=store)
    budget_exhausted = False
    evidence_contract_error: ProviderEvidenceContractError | None = None
    try:
        runner.run(dataset=dataset, runs_per_case=args.runs_per_case)
    except Exception as exc:
        if _is_budget_exhausted(exc):
            budget_exhausted = True
        elif isinstance(exc, ProviderEvidenceContractError):
            evidence_contract_error = exc
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

    evidence_totals = _aggregate_provider_evidence(store)
    hard_stop_conditions = list(manifest.get("hard_stop_conditions", []))
    if evidence_contract_error is not None:
        hard_stop_conditions.append(evidence_contract_error.hard_stop_condition)

    manifest.update(
        {
            "updated_at": _utc_now(),
            "completed_attempts": len(attempts),
            **evidence_totals,
            "last_command_provider_invocations": budget.used,
            "decision": (
                "BLOCKED_PROVIDER_EVIDENCE_CONTRACT"
                if evidence_contract_error is not None
                else "INCOMPLETE"
                if budget_exhausted
                else metrics.decision
            ),
            "hard_stop_conditions": list(dict.fromkeys(hard_stop_conditions)),
            "terminal": evidence_contract_error is not None,
            "provider_called": evidence_totals["provider_invocations"] > 0,
            "first_data_request_sent": evidence_totals["provider_invocations"] > 0,
        }
    )
    if evidence_contract_error is not None:
        manifest["preflight_status"] = "terminal"
    store.write_manifest(manifest)
    print(f"run_id={manifest['run_id']}")
    print(f"run_dir={store.run_dir}")
    print(f"completed_attempts={len(attempts)}/{expected_attempts}")
    print(f"provider_invocations_this_run={budget.used}/{budget.limit}")
    print(
        "provider_invocations_cumulative="
        f"{prior_used + budget.used}/{budget.limit}"
    )

    if evidence_contract_error is not None:
        return 2
    if budget_exhausted and len(attempts) < expected_attempts:
        return 2
    if metrics.decision == "INSUFFICIENT_SAMPLE":
        return 2
    return 0 if metrics.passed else 1


def _aggregate_provider_evidence(store: EvaluationArtifactStore) -> dict:
    records = list(store.load_normalized_attempts())
    records.extend(
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(store.run_dir.glob("attempts/*/run-*/error.json"))
    )
    totals = {
        "provider_invocations": 0,
        "provider_metered_invocations": 0,
        "provider_retries": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cached_input_tokens": 0,
        "actual_provider_models": [],
        "provider_response_id_sha256s": [],
    }
    for record in records:
        for key in (
            "provider_invocations",
            "provider_metered_invocations",
            "provider_retries",
            "input_tokens",
            "output_tokens",
            "cached_input_tokens",
        ):
            value = record.get(key, 0)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                totals[key] += value
        models = record.get("actual_provider_models", [])
        if not models and isinstance(record.get("actual_provider_model"), str):
            models = [record["actual_provider_model"]]
        for model in models:
            if model not in totals["actual_provider_models"]:
                totals["actual_provider_models"].append(model)
        for value in record.get("provider_response_id_sha256s", []):
            totals["provider_response_id_sha256s"].append(value)
    totals["actual_provider_model"] = (
        totals["actual_provider_models"][0]
        if len(totals["actual_provider_models"]) == 1
        else None
    )
    totals["provider_response_id_count"] = len(
        totals["provider_response_id_sha256s"]
    )
    totals["provider_usage_complete"] = (
        totals["provider_invocations"] > 0
        and totals["provider_metered_invocations"]
        == totals["provider_invocations"]
        and totals["provider_response_id_count"]
        == totals["provider_invocations"]
        and totals["actual_provider_model"] is not None
    )
    return totals


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
    prompt_sha256: str,
    max_provider_invocations: int,
    candidate_fields: dict,
    gate_config_sha256: str,
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
        "prompt_sha256": prompt_sha256,
        "rubric_version": REPORT_SCORING_RUBRIC_VERSION,
        "rubric_sha256": REPORT_SCORING_RUBRIC_SHA256,
        "authorization_id": authorization_id,
        "authorization_sha256": authorization_sha256,
        "authorization_receipt": authorization_receipt,
        "authorization_receipt_sha256": authorization_receipt_sha256,
        "max_provider_invocations": max_provider_invocations,
        "summary_prompt_version": REPORT_SUMMARY_PROMPT_VERSION,
        "summary_prompt_sha256": REPORT_SUMMARY_PROMPT_SHA256,
        "answer_guidance_version": REPORT_ANSWER_GUIDANCE_VERSION,
        "gate_config_sha256": gate_config_sha256,
        "partition_mode": PARTITION_MODE,
        "blind_test_consumed": False,
        "provider_evidence_contract_version": PROVIDER_EVIDENCE_CONTRACT_VERSION,
        "ownership_contract_version": OWNERSHIP_CONTRACT_VERSION,
        "attempt_schema_version": ATTEMPT_SCHEMA,
    }
    expected.update(candidate_fields)
    for key, value in expected.items():
        if manifest.get(key) != value:
            raise SystemExit(f"resume manifest mismatch for {key}")


def _prompt_sha256() -> str:
    source = inspect.getsource(OpenAIInterviewLLM._build_report_prompt)
    actual = hashlib.sha256(
        f"{REPORT_EVIDENCE_PROMPT_VERSION}\n{source}".encode("utf-8")
    ).hexdigest()
    if actual != REPORT_EVIDENCE_PROMPT_SHA256:
        raise SystemExit("report evidence prompt integrity check failed")
    return actual


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
